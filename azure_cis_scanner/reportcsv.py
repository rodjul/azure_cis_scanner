import csv
from json import load
import os
import yaml
import functools
# import utils

FILE = "reports.csv"
PATH_FOLDER_SCAN_DATA = ""
CIS_STRUCTURE = {}
CSV_HEADER = ["Subscription", "Service", "CIS Reference", "Item", "In Compliance","Total checks", "Total not in compliance", "Results", "Metadata Columns"]


def load_CIS_STRUCTURE():
    # set paths depending on if we are in the container or not

    # if os.path.exists(os.path.expanduser('~/engagements/cis_test/scans')):
    #     scans_base = os.path.expanduser('~/engagements/cis_test/scans')
    # elif os.path.exists('/engagements/cis_test/scans'):
    #     scans_base = '/engagements/cis_test/scans'
    # else:
    #     scans_base = os.path.join(os.getcwd(), 'scans')

    #accounts = {}
    # accounts_path = os.path.join(scans_base, 'accounts.json')
    # with open(accounts_path, 'r') as f:
    #     accounts = yaml.load(f)

    APP_ROOT = os.path.dirname(os.path.abspath(__file__))
    STATIC = os.path.join(APP_ROOT, 'static')
    path = os.path.abspath(os.path.expanduser(APP_ROOT + '/report/CIS_STRUCTURE.yaml'))
    with open(path, 'r') as f:
        CIS_STRUCTURE = yaml.load(f, Loader=yaml.Loader)
    
    return CIS_STRUCTURE

def add_data(data):
    with open(FILE, "a", newline='\n') as f:
        csvw = csv.writer(f,  delimiter=',', quoting=csv.QUOTE_ALL)
        subscription = data['subscription']
        service = data['service']
        reference = data['reference']
        item = data['item']
        results = data['results']

        items_checked = 0
        items_flagged = 0
        if results:
            items_checked = results['stats']['items_checked']
            items_flagged = results['stats']['items_flagged']
        
        compliance = False
        if items_flagged == 0:
            compliance = True
        if items_checked == 0:
            compliance = "N/A"

        # print(results)
        if results:
            for result in results['items']:
                csvw.writerow([subscription, service, reference, item, compliance, items_checked, items_flagged, result, results['metadata']['columns'] ])
        else:
            csvw.writerow([subscription, service, reference, item, compliance, items_checked, items_flagged, results, "" ])

def list_scans_folders_reports(path):
    path = os.path.abspath(path)
    if os.path.exists(path):
        contents = os.listdir(path)
        folders = [i for i in contents if os.path.isdir(os.path.abspath(path+"/"+i))]
        return folders
    return []



def get_dirs(directory):
    return list(reversed(sorted([x for x in os.listdir(directory) if os.path.isdir(directory) and not x.endswith('.DS_Store')])))

def get_stats(scans_data_dir):
    stats = {}
    # dir_list = get_dirs(scans_data_dir)
    dir_list = list_scans_folders_reports(scans_data_dir)
    # print("get_stats dir_list {}".format(dir_list))
    for section_name in CIS_STRUCTURE['section_ordering']:
        stats[section_name] = {}
        section_name_file = '_'.join(map(str.lower, section_name.split(' '))) + '_filtered.json'
        for dir_date in dir_list:
            filtered_data_path = os.path.join(scans_data_dir, dir_date, 'filtered', section_name_file)
            if os.path.exists(filtered_data_path):
                with open(filtered_data_path, 'r') as f:
                    data = yaml.load(f, Loader=yaml.Loader)
                for finding_name, finding_data in data.items():
                    if not finding_name in stats[section_name]:
                        stats[section_name][finding_name] = {}
                    stats[section_name][finding_name][dir_date] = finding_data['stats']
    return stats


def get_latest_stats(scans_data_dir):
    latest_stats = {}
    stats = get_stats(scans_data_dir)
    # print("get_latest_stats:get_stats: {}".format(stats))
    for section_name in stats:
        latest_stats[section_name] = {}
        for finding_name in stats[section_name]:
            date = max(stats[section_name][finding_name])
            latest_stats[section_name][finding_name] = {"date": date, **stats[section_name][finding_name][date]}

    # print("get_latest_stats:latest_stats: {}".format(latest_stats))
    return latest_stats


def get_finding_name(finding_name, subsection_name):
    """
    Get finding name from CIS_TOC using over-ride (finding_name) but defaulting to parsed subsection_name
    """
    if finding_name:
        return finding_name
    else:
        return underscore_name(subsection_name)

def underscore_name(subsection_name):
    return '_'.join(map(lambda x: x.lower(), subsection_name.split(' ')))

def get_finding_index(findings_list, finding):
    for finding_entry in findings_list:
        if finding_entry['subsection_name'] == finding:
            return finding_entry
    raise ValueError("finding {} not found in {}".format(finding, findings_list))

def get_filtered_data_by_name(scans_data_dir, section_name, date=None):
    """
    Get the latest data for a section returning first found <= date
    @params sectoin_name: Name of CIS section as a string e.g. ("Identity and Access Management")
    @params date: date in format 'YYYY-M-D', i.e. strftime("%Y-%m-%d")
    @returns filtered data, date
    """
    # get date folders, most to least recent
    dir_list = get_dirs(scans_data_dir)
    # print("get_filtered_data_by_name:dir_list: {}".format(dir_list))
    section_name_file = '_'.join(map(str.lower, section_name.split(' '))) + '_filtered.json'
    for dir_date in dir_list:
        if date and (dir_date > date):
            continue
        filtered_data_path = os.path.join(scans_data_dir, dir_date, 'filtered', section_name_file)
        if os.path.exists(filtered_data_path):
            # print('get_filtered_data_by_name:filtered_data_path', filtered_data_path)
            with open(filtered_data_path, 'r') as f:
                data = yaml.load(f, Loader=yaml.Loader)
                # We might have, for example, deleted all the databases.  Keep going till we find data.
                if data:
                    for key,val in data.items():
                        data[key]['date'] = dir_date
                    return data
    else:
        return None


def get_data(service, finding):
    """
    Render the non-graph portion of the finding as a table of the latest date recording this finding
    The graph portion is rendered in plot_finding
    """
    # print(service, finding)
    finding_entry = get_finding_index(CIS_STRUCTURE['TOC'][service], finding)
    service_data = get_filtered_data_by_name(PATH_FOLDER_SCAN_DATA, service)
    # print("finding:service_data: {}".format(service_data))
    error_str = ''
    if not service_data:
        error_str = 'No section named "{}" found\n'.format(service)
        return ""
    finding_name = get_finding_name(finding_entry['finding_name'], finding_entry['subsection_name'])
    finding_data = service_data.get(finding_name, None)
    # print("finding_data {}".format(finding_data))
    # date = finding_data.get('date', 'No Date')
    return finding_data


def main():
    global PATH_FOLDER_SCAN_DATA

    with open(FILE, "w", newline='\n') as f:
        csvw = csv.writer(f, delimiter=',', quoting=csv.QUOTE_ALL)
        csvw.writerow(CSV_HEADER)


    for subscription in list_scans_folders_reports("..\scans"):
        PATH_FOLDER_SCAN_DATA = "..\scans\{}".format(subscription)
        # path_folder_scan_data = "..\scans"
        global CIS_STRUCTURE
        CIS_STRUCTURE = load_CIS_STRUCTURE()
        
        data = get_latest_stats(PATH_FOLDER_SCAN_DATA)
        # print(data)
        # exit(0)
        cis_headers = CIS_STRUCTURE['TOC']
        # print(CIS_STRUCTURE['section_ordering'])
        # print(CIS_STRUCTURE['subsection_ordering'])
        # print(CIS_STRUCTURE['audit_subsction_ordering'])
        
        # output = []
        for service in cis_headers:
            findings_table = [(x['subsection_number'], x['subsection_name'], get_finding_name(x['finding_name'], x['subsection_name'])) for x in CIS_STRUCTURE['TOC'][service]]
            # print(findings_table)
            for finding in findings_table:
                # print(finding)
                report = get_data(service, finding[1])
                subscription_name = ''.join(subscription.replace("_"," ").split("-")[:len(subscription.split("-"))-1])
                format_data = {
                    "subscription": subscription_name,
                    "service": service,
                    "reference": finding[0],
                    "item":finding[1],
                    "results": report
                }

                add_data(format_data)
                # output.append(format_data)
                # print(format_data)
                # input()
                # print(report)
        # print(data)



if __name__ == "__main__":
    main()