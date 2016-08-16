import sys, os, pwd, grp, signal, time, glob, socket
from resource_management import *
from subprocess import call
from setup_ranger_nifi import setup_ranger_nifi

reload(sys)
sys.setdefaultencoding('utf8')

class Master(Script):
  def install(self, env):

    import params
    import status_params

    self.install_packages(env)

    #Create user and group if they don't exist
    self.create_linux_user(params.nifi_user, params.nifi_group)

    #update the configs specified by user
    self.configure(env, True)

    Execute('touch ' +  params.nifi_node_log_file, user=params.nifi_user)


  def create_linux_user(self, user, group):
    try: pwd.getpwnam(user)
    except KeyError: Execute('adduser ' + user)
    try: grp.getgrnam(group)
    except KeyError: Execute('groupadd ' + group)



  def configure(self, env, isInstall=False):
    import params
    import status_params
    env.set_params(params)
    env.set_params(status_params)

    #create the log, pid, conf dirs if not already present
    Directory([status_params.nifi_pid_dir, params.nifi_node_log_dir, params.nifi_internal_dir, params.nifi_database_dir, params.nifi_flowfile_repo_dir, params.nifi_content_repo_dir_default, params.nifi_provenance_repo_dir_default, params.nifi_config_dir, params.nifi_flow_config_dir, params.nifi_state_dir, params.lib_dir],
            owner=params.nifi_user,
            group=params.nifi_group,
            create_parents=True,
            recursive_ownership=True
    )

    # On some OS this folder may not exist, so we will create it before pushing files there
    Directory(params.limits_conf_dir,
              create_parents = True,
              owner='root',
              group='root'
    )

    File(os.path.join(params.limits_conf_dir, 'nifi.conf'),
         owner='root',
         group='root',
         mode=0644,
         content=Template("nifi.conf.j2")
    )

    
    #write out nifi.properties

    PropertiesFile(params.nifi_config_dir + '/nifi.properties',
                   properties = params.nifi_properties,
                   mode = 0400,
                   owner = params.nifi_user,
                   group = params.nifi_group)


    #write out boostrap.conf
    bootstrap_content=InlineTemplate(params.nifi_boostrap_content)
    File(format("{params.nifi_config_dir}/bootstrap.conf"), content=bootstrap_content, owner=params.nifi_user, group=params.nifi_group, mode=0400)

    #write out logback.xml
    logback_content=InlineTemplate(params.nifi_node_logback_content)
    File(format("{params.nifi_config_dir}/logback.xml"), content=logback_content, owner=params.nifi_user, group=params.nifi_group, mode=0400)

    #write out state-management.xml
    statemgmt_content=InlineTemplate(params.nifi_state_management_content)
    File(format("{params.nifi_config_dir}/state-management.xml"), content=statemgmt_content, owner=params.nifi_user, group=params.nifi_group, mode=0400)

    #write out authorizers file
    authorizers_content=InlineTemplate(params.nifi_authorizers_content)
    File(format("{params.nifi_config_dir}/authorizers.xml"), content=authorizers_content, owner=params.nifi_user, group=params.nifi_group, mode=0400)

    #write out login-identity-providers.xml
    login_identity_providers_content=InlineTemplate(params.nifi_login_identity_providers_content)
    File(format("{params.nifi_config_dir}/login-identity-providers.xml"), content=login_identity_providers_content, owner=params.nifi_user, group=params.nifi_group, mode=0400)

    #write out nifi-env in bin as 0755 (see BUG-61769)
    env_content=InlineTemplate(params.nifi_env_content)
    File(format("{params.bin_dir}/nifi-env.sh"), content=env_content, owner=params.nifi_user, group=params.nifi_group, mode=0755) 
    
    #write out bootstrap-notification-services.xml
    boostrap_notification_content=InlineTemplate(params.nifi_boostrap_notification_content)
    File(format("{params.nifi_config_dir}/bootstrap-notification-services.xml"), content=boostrap_notification_content, owner=params.nifi_user, group=params.nifi_group, mode=0400) 



  def stop(self, env):
    import params
    import status_params

    Execute ('export JAVA_HOME='+params.jdk64_home+';'+params.bin_dir+'/nifi.sh stop >> ' + params.nifi_node_log_file, user=params.nifi_user)
    if os.path.isfile(status_params.nifi_node_pid_file):
      Execute ('rm ' + status_params.nifi_node_pid_file)



  def start(self, env):
    import params
    import status_params
    self.configure(env)
    setup_ranger_nifi(upgrade_type=None)

    # Write out flow.xml.gz to internal dir only if AMS installed (must be writable by Nifi)
    # only during first install. It is used to automate setup of Ambari metrics reporting task in Nifi
    if params.metrics_collector_host and params.nifi_ambari_reporting_enabled and self.check_is_fresh_install(self):
      Execute('echo "First time setup so generating flow.xml.gz" >> ' + params.nifi_node_log_file, user=params.nifi_user)
      flow_content=InlineTemplate(params.nifi_flow_content)
      File(format("{params.nifi_flow_config_dir}/flow.xml"), content=flow_content, owner=params.nifi_user, group=params.nifi_group, mode=0600)
      Execute(format("cd {params.nifi_flow_config_dir}; mv flow.xml.gz flow_$(date +%d-%m-%Y).xml.gz ;"),user=params.nifi_user,ignore_failures=True)
      Execute(format("cd {params.nifi_flow_config_dir}; gzip flow.xml;"), user=params.nifi_user)


    Execute ('export JAVA_HOME='+params.jdk64_home+';'+params.bin_dir+'/nifi.sh start >> ' + params.nifi_node_log_file, user=params.nifi_user)
    #If nifi pid file not created yet, wait a bit
    if not os.path.isfile(status_params.nifi_pid_dir+'/nifi.pid'):
      Execute ('sleep 5')


  def status(self, env):
    import status_params
    check_process_status(status_params.nifi_node_pid_file)


  def check_is_fresh_install(self, env):
    """
    Checks if fresh nifi install by checking if zk dir exists
    :return:
    """
    import params, re
    from resource_management.core import shell
    from resource_management.core.exceptions import Fail
    from resource_management.core.logger import Logger

    ZK_CONNECT_ERROR = "ConnectionLoss"
    ZK_NODE_NOT_EXIST = "Node does not exist"

    zookeeper_queried = False
    is_fresh_nifi_install = True

    # For every zk server try to find nifi zk dir
    zookeeper_server_list = params.config['clusterHostInfo']['zookeeper_hosts']
    for zookeeper_server in zookeeper_server_list:
      # Determine where the zkCli.sh shell script is
      zk_command_location = os.path.join(params.stack_root, "current", "zookeeper-client", "bin", "zkCli.sh")
      if params.stack_version_buildnum is not None:
        zk_command_location = os.path.join(params.stack_root, params.stack_version_buildnum, "zookeeper", "bin", "zkCli.sh")

      # create the ZooKeeper query command e.g.
      # /usr/hdf/current/zookeeper-client/bin/zkCli.sh -server node:2181 ls /nifi
      command = "{0} -server {1}:{2} ls {3}".format(
        zk_command_location, zookeeper_server, params.zookeeper_port, params.nifi_znode)
              
      # echo 'ls /nifi' | /usr/hdf/current/zookeeper-client/bin/zkCli.sh -server node:2181
      #command = "echo 'ls {3}' | {0} -server {1}:{2}".format(
      #  zk_command_location, zookeeper_server, params.zookeeper_port, params.nifi_znode)

      Logger.info("Running command: " + command)

      code, out = shell.call(command, logoutput=True, quiet=False, timeout=20)
      if not out or re.search(ZK_CONNECT_ERROR, out):
        Logger.info("Unable to query Zookeeper: " + zookeeper_server + ". Skipping and trying next ZK server")
        continue
      elif re.search(ZK_NODE_NOT_EXIST, out):
        Logger.info("Nifi ZNode does not exist, so must be fresh install of Nifi: " + params.nifi_znode)
        zookeeper_queried = True
        is_fresh_nifi_install = True
        break
      else:
        Logger.info("Nifi ZNode already exists, so must not be a fresh install of Nifi: " + params.nifi_znode)
        zookeeper_queried = True
        is_fresh_nifi_install = False
        break

    # fail if the ZK data could not be queried
    if not zookeeper_queried:
      raise Fail("Unable to query for znode on on any of the following ZooKeeper hosts: {0}. Please ensure Zookeepers are started and retry".format(
        zookeeper_server_list))
    else:
      return is_fresh_nifi_install    
            

if __name__ == "__main__":
  Master().execute()
