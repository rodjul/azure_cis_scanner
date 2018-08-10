
import datetime
import os
import subprocess
import sys
import re
import json
import requests
import traceback
import yaml

# azure_cis_scanner.credentials is a modification of https://github.com/Azure/azure-sdk-for-python/blob/master/azure-common/azure/common/credentials.py
# until https://github.com/Azure/azure-sdk-for-python/issues/2898 gets fixed
from azure.common.client_factory import get_client_from_cli_profile, get_client_from_auth_file
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.sql import SqlManagementClient
from azure.mgmt.monitor import MonitorManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.monitor.models import RuleMetricDataSource
from azure.mgmt.resource import ResourceManagementClient, SubscriptionClient
from azure.common.credentials import ServicePrincipalCredentials

from azure_cis_scanner.credentials import get_azure_cli_credentials

AZURE_CONFIG_DIR = os.path.expanduser('~/.azure')
AZURE_PROFILE_PATH = os.path.join(AZURE_CONFIG_DIR, 'azureProfile.json')
AZURE_CREDENTIALS_PATH = os.path.join(AZURE_CONFIG_DIR, 'credentials')
AZURE_SERVICE_PRINCIPALS_PATH = os.path.join(AZURE_CONFIG_DIR, 'servicePrincipals.json')

# https://github.com/Azure/azure-cli/blob/dev/src/command_modules/azure-cli-keyvault/azure/cli/command_modules/keyvault/_client_factory.py
def keyvault_data_plane_factory(cli_ctx, _):
    from azure.keyvault import KeyVaultAuthentication, KeyVaultClient
    from azure.cli.core.profiles import ResourceType, get_api_version
    version = str(get_api_version(cli_ctx, ResourceType.DATA_KEYVAULT))

    def get_token(server, resource, scope):  # pylint: disable=unused-argument
        import adal
        from azure.cli.core._profile import Profile
        try:
            return Profile(cli_ctx=cli_ctx).get_raw_token(resource)[0]
        except adal.AdalError as err:
            from knack.util import CLIError
            # pylint: disable=no-member
            if (hasattr(err, 'error_response') and
                    ('error_description' in err.error_response) and
                    ('AADSTS70008:' in err.error_response['error_description'])):
                raise CLIError(
                    "Credentials have expired due to inactivity. Please run 'az login'")
            raise CLIError(err)

    return KeyVaultClient(KeyVaultAuthentication(get_token), api_version=version)

def get_service_principal_credentials(auth_type='sdk'):
    """
    Get service principal credentials required by KeyVault, Storage 
    """
    if os.path.exists(AZURE_SERVICE_PRINCIPALS_PATH) and (os.stat(AZURE_SERVICE_PRINCIPALS_PATH).st_size != 0):
        with open(AZURE_SERVICE_PRINCIPALS_PATH, 'r') as f:
            creds = json.loads(f.read())
        return creds
    
    if auth_type == 'sdk':
        credentials = json.loads(call("az ad sp create-for-rbac --sdk-auth", stderr=None))
        sp_credentials = ServicePrincipalCredentials(
            client_id=credentials['clientId'],
            secret=credentials['clientSecret'],
            tenant=credentials['tenantId']
        )

    with open(AZURE_SERVICE_PRINCIPALS_PATH, 'w') as f:
        f.write(json.dumps(credentials))
    return sp_credentials

def get_credentials_from_cli(tenant_id=None, subscription_id=None):
    """
    Create a credential for each of the subscriptions in azureProfile.json for a tenant
    @param tenant_id: uuid string - if None, iterate over all tenant_ids
    @param subscription_id: uuid string - if None iterate over all subscription_ids
    @returns: list of (tenant_id, subscription_id, subscription_name, credentials) where credentials is an ADAL signed session 
              bound to the tenant_id and subscription
    """

    with open(AZURE_PROFILE_PATH, 'r') as f:
        azure_profiles = yaml.load(f)['subscriptions']
    results = []
    for profile in azure_profiles:
        if tenant_id and not (tenant_id == profile['tenantId']):
            continue
        tenant_id = profile['tenantId']
        if subscription_id and (subscription_id != profile['id']):
            continue
        subscription_id = profile['id']
        subscription_name = profile['name']
        service_principle_name = profile['name'] + '-' + subscription_id
        
        # this is a modification of https://github.com/Azure/azure-sdk-for-python/blob/master/azure-common/azure/common/credentials.py
        # until https://github.com/Azure/azure-sdk-for-python/issues/2898 gets fixed
        print('get_clients_from_cli', subscription_id, tenant_id)
        credentials = get_azure_cli_credentials(resource=None, with_tenant=False, subscription_id=subscription_id)[0]
        
        results.append((tenant_id, subscription_id, subscription_name, credentials))
    return results

def get_clients_from_cli(subscription_id):
    credentials, subscription_id, tenant_id = get_azure_cli_credentials(resource=None, with_tenant=True, subscription_id=subscription_id)

    print("creating subscription client")
    subscription_client = SubscriptionClient(credentials)
    print("creating compute client")
    compute_client = ComputeManagementClient(credentials, subscription_id)
    print("creating sql client")
    sql_client = SqlManagementClient(credentials, subscription_client)
    print("creating resource manager client")
    rm_client = ResourceManagementClient(credentials, subscription_id)

    return subscription_client, compute_client, rm_client, sql_client

def get_list_from_paged_results(paged):
    results = []
    results.extend(paged.advance_page())
    while paged.next_link:
        results.extend(paged.advance_page)
    return [x.as_dict() for x in results]

def get_clients_from_service_principals(tenant_id=None, generate_credentials_ini=False, generate_auth_file=False, overwrite_ini=False):
    """
    Create a client for each of the subscriptions in azureProfile.json for a tenant
    """
    credentials_ini = ""

    with open(AZURE_PROFILE_PATH, 'r') as f:
        azure_profiles = yaml.load(f)['subscriptions']
    for profile in azure_profiles:
        if tenant_id and not (tenant_id == profile['tenantId']):
            continue
        subscription_id = profile['id']
        service_principle_name = profile['name'] + '-' + subscription_id
        result = call("az account set --subscription {}".format(subscription_id))
        print(result)
        credentials = jsonify(call("az ad sp create-for-rbac --sdk-auth"))
        print(credentials)

        credentials_ini += credentials_block(subscription_id, credentials, service_principle_name)
        if generate_auth_file:
            azure_auth_location = os.path.join(AZURE_CONFIG_DIR, service_principle_name + '.json')
            with open(azure_auth_location, 'w') as f:
                f.write(credentials)

        sp_credentials = ServicePrincipalCredentials(
            client_id=credentials['clientId'],
            secret=credentials['clientSecret'],
            tenant=credentials['tenantId']
        )
        if generate_credentials_ini:
            if overwrite_ini:
                mode = 'w'
            else:
                mode = 'w+'
            with open(AZURE_CREDENTIALS_PATH, mode) as f:
                f.write(credentials_ini)

        subscription_client = SubscriptionClient(credentials, subscription_id)
        compute_client = ComputeManagementClient(credentials, subscription_id)
        sql_client = SqlManagementClient(credentials, subscription_id)

        yield subscription_client, compute_client, sql_client


def credentials_block(subscription_id, credentials, service_principal_name, is_default=False):
    """
    Generate a block for use in ~/.azure/credentials ini file
    """
    client_id = credentials['client']
    secret = credentials['secret']
    tenant_id = credentials['tenant']

    ini_content = ""

    if is_default:
        ini_content += """ 
[default]
subscription_id={}
client_id={}
secret={}
tenant={}
""".format(subscription_id, client_id, secret, tenant_id)

    else:
        ini_content += """ 
[{}]
subscription_id={}
client_id={}
secret={}
tenant={}
""".format(service_principal_name, subscription_id, client_id, secret, tenant_id)
    return ini_content


def get_resource_groups(client, subscription_id, resource_groups_path):
    groups = []
    rgs = client.resource_groups.list()
    groups.extend(rgs.advance_page())
    while rgs.next_link:
        groups.extend(rgs.advance_page)
    resource_groups = [x.as_dict() for x in groups]

    with open(resource_groups_path, 'w') as f:
        json.dump(resource_groups, f, indent=4, sort_keys=True)
    return resource_groups

def load_resource_groups(resource_groups_path):
    with open(resource_groups_path, 'r') as f:
        resource_groups = yaml.safe_load(f)
    return resource_groups

token_expiry = None
access_token = None
filtered_data_dir = ''
scan_data_dir = ''
raw_data_dir = ''

def set_data_paths(subscription_dirname, base_dir='.'):
    """
    Given a base_dir, create subdirs scans/{day}/raw
                                          /filtered
    @returns: scan_data_dir, raw_data_dir
    """
    # Get day in YYYY-MM-DD format

    day = datetime.datetime.now().strftime('%Y-%m-%d')

    if base_dir.startswith('/'):
        base_dir = os.path.abspath(base_dir)
    elif base_dir.startswith('~'):
        base_dir = os.path.expanduser(base_dir)
    scan_data_dir = os.path.join(base_dir, 'scans', subscription_dirname, day)
    print("scan_data_dir", scan_data_dir)
    raw_data_dir = scan_data_dir + '/raw'
    print("raw_data_dir", raw_data_dir)
    if not os.path.exists(raw_data_dir):
        os.makedirs(raw_data_dir)
    filtered_data_dir = scan_data_dir + '/filtered'
    print("filtered_data_dir", filtered_data_dir)
    if not os.path.exists(filtered_data_dir):
        os.makedirs(filtered_data_dir)
    return scan_data_dir, raw_data_dir, filtered_data_dir   


def call(command, retrieving_access_token=False, stderr=None):
    #if not valid_token() and not retrieving_access_token:
    #    get_access_token()
    if(isinstance(command, str)) :
        command = command.split()           # subprocess needs an array of arguments
    try :
        print('running: ', command)
        result = subprocess.check_output(command, shell=False, stderr=stderr).decode('utf-8')
        print("result", result)
        return result
    # allow calling code to raise AzScannerException and continue
    except AzScannerException as e:
        print("An exception occurred while processing command " + str(command) )
        print(e)
        print(traceback.format_exc())
        raise(e)


def verify_subscription_id_format(subscriptionId) :
    r = re.compile("([a-f]|[0-9]){8}-([a-f]|[0-9]){4}-([a-f]|[0-9]){4}-([a-f]|[0-9]){4}-([a-f]|[0-9]){12}")
    if r.match(subscriptionId):
        return True
    else :
        return False


def valid_token():
     if (not token_expiry) or (datetime.datetime.utcnow() > token_expiry):
        return False
     else:
        return True


def get_active_account():
    return jsonify(call("az account show"))


def get_subscription_id(account) :
    return account["id"]


def get_subscription_name(subscription_id, accounts):
    for account in accounts:
        if subscription_id == accounts['id']:
            return accounts['name']
    raise ValueError("subscription_id {} not found in accounts {}".format(subscription_id, accounts))


def get_access_token():
    global token_expiry, access_token
    if not valid_token():
        complete_token = call("az account get-access-token", retrieving_access_token=True)
        complete_token = jsonify(complete_token)
        access_token = complete_token["accessToken"]
        token_expiry = complete_token["expiresOn"]
        print(token_expiry)
        token_expiry = datetime.datetime.strptime(token_expiry, '%Y-%m-%d %H:%M:%S.%f')
    return access_token, token_expiry


def make_request(url, args=[]):
    print('requesting ', url)
    authorization_headers = {"Authorization" : "Bearer " + get_access_token()}
    r = requests.get(url, headers=authorization_headers)
    return r.text


def jsonify(jsonString) :
    return json.loads(jsonString)


def stringify(jsonObject) :
    return json.dumps(jsonObject)


class AzScannerException(Exception):
    def __init__(self,*args,**kwargs):
        Exception.__init__(self,*args,**kwargs)