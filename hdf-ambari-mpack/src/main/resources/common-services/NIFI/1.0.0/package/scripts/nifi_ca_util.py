import json, nifi_constants, os
from resource_management.core import sudo
from resource_management.core.resources.system import File

script_dir = os.path.dirname(__file__)
files_dir = os.path.realpath(os.path.join(os.path.dirname(script_dir), 'files'))

def load(config_json):
  if sudo.path_isfile(config_json):
    contents = sudo.read_file(config_json)
    if len(contents) > 0:
      return json.loads(contents)
  return {}

def dump(config_json, config_dict):
  import params
  File(config_json,
    owner=params.nifi_user,
    group=params.nifi_group,
    mode=0600,
    content=json.dumps(config_dict, sort_keys=True, indent=4)
  ) 

def overlay(config_dict, overlay_dict):
  for k, v in overlay_dict.iteritems():
    if v or k not in config_dict:
      config_dict[k] = v

def get_toolkit_script(scriptName, scriptDir = files_dir):
  nifiToolkitDir = None
  for dir in os.listdir(scriptDir):
    if dir.startswith('nifi-toolkit-'):
      nifiToolkitDir = os.path.join(scriptDir, dir)

  if nifiToolkitDir is None:
    raise Exception("Couldn't find nifi toolkit directory in " + scriptDir)
  result = nifiToolkitDir + '/bin/' + scriptName
  if not sudo.path_isfile(result):
    raise Exception("Couldn't find file " + result)
  return result

def update_nifi_properties(client_dict, nifi_properties):
  nifi_properties[nifi_constants.NIFI_SECURITY_KEYSTORE_TYPE] = client_dict['keyStoreType']
  nifi_properties[nifi_constants.NIFI_SECURITY_KEYSTORE_PASSWD] = client_dict['keyStorePassword']
  nifi_properties[nifi_constants.NIFI_SECURITY_KEY_PASSWD] = client_dict['keyPassword']
  nifi_properties[nifi_constants.NIFI_SECURITY_TRUSTSTORE_TYPE] = client_dict['trustStoreType']
  nifi_properties[nifi_constants.NIFI_SECURITY_TRUSTSTORE_PASSWD] = client_dict['trustStorePassword']

def store_exists(client_dict, key):
  if key not in client_dict:
    return False
  return sudo.path_isfile(client_dict[key])

def different(one, two, key):
  if key not in one:
    return False
  if len(one[key]) == 0:
    return False
  if key not in two:
    return False
  if len(two[key]) == 0:
    return False
  return one[key] != two[key]

def move_keystore_truststore_if_necessary(orig_client_dict, new_client_dict):
  if not (store_exists(new_client_dict, 'keyStore') or store_exists(new_client_dict, 'trustStore')):
    return
  if different(orig_client_dict, new_client_dict, 'keyStoreType'):
    move_keystore_truststore(new_client_dict)
  elif different(orig_client_dict, new_client_dict, 'keyStorePassword'):
    move_keystore_truststore(new_client_dict)
  elif different(orig_client_dict, new_client_dict, 'keyPassword'):
    move_keystore_truststore(new_client_dict)
  elif different(orig_client_dict, new_client_dict, 'trustStoreType'):
    move_keystore_truststore(new_client_dict)
  elif different(orig_client_dict, new_client_dict, 'trustStorePassword'):
    move_keystore_truststore(new_client_dict)

def move_keystore_truststore(client_dict):
  move_store(client_dict, 'keyStore')
  move_store(client_dict, 'trustStore')

def move_store(client_dict, key):
  if store_exists(client_dict, key):
    num = 0
    name = client_dict[key]
    while sudo.path_isfile(name + '.bak.' + str(num)):
      num += 1
    sudo.copy(name, name + '.bak.' + str(num))
    sudo.unlink(name)
