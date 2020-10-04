"""
Stress tests for switching the master storage domain.

Usage:

1. Have two storage domains: one is the current master domain,
and the other will be the new master domain.
2. Run this on the SPM host:

    python3 switch_master.py --pool-id XXX --new-master YYY --old-master ZZZ

If there were no errors, the master role switched to the specified new-master
storage domain.

In order to run the script:
- Stop the engine
- Run the switch-master script, specifying the pool-id, old-master,
  and new-master parameters:
    python3 switch_master.py --pool-id 11d54412-9232-43af-a51c-74078e7d03ce
      --new-master d165c4d9-eae1-44cc-ad16-07ea595c383f
      --old-master c5fabee4-b350-4393-8964-8437278ff70f
- Edit the engine DB by setting the storage_domain_type for the new master
  to '0' (Master), the old master to '1' (Data), and update the pool's
  master-version:
    $ sudo -u postgres psql -d engine
    # update storage_domain_static set storage_domain_type = 0
      where id = 'new-master';
    # update storage_domain_static set storage_domain_type = 1
      where id = 'old-master';
    # update storage_pool set master_domain_version = master-version
      where id = 'pool-id';
- Start the engine and verify 'reconstructMaster' isn't being called,
  and the new master is now the master.

WARNING: The script runs only on the SPM.
Once the SPM stops, the script fails with "Not SPM" message.

NOTE: Once the switchMaster command is being performed and the Master domain
not synced between the engine's DB and VDSM, the engine will reconstruct
the master domain and overwrite the masterVersion.

See https://bugzilla.redhat.com/1576923
"""

import argparse
import logging
import time
from contextlib import closing

from vdsm import client

log = logging.getLogger()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-7s (%(threadName)s) %(message)s")
    old_master = args.old_master
    new_master = args.new_master

    cli = client.connect("localhost", 54321)
    with closing(cli):
        if args.master_version:
            master_ver = args.master_version
        else:
            pool_info = cli.StoragePool.getInfo(storagepoolID=args.pool_id)
            master_ver = int(pool_info['info']['master_ver']) + 1

        for i in range(1, args.iterations + 1):
            log.info("Cycle %s/%s, switching master from %s to %s version %s",
                     i, args.iterations, old_master, new_master, master_ver)
            task_id = cli.StoragePool.switchMaster(
                storagepoolID=args.pool_id,
                oldMasterUUID=old_master,
                newMasterUUID=new_master,
                masterVersion=master_ver)
            log.info("Task id: %s", task_id)

            # Example Task.getStatus response:
            # {'taskID': '5e7b6cd0-d9d7-4e48-b525-7f1f0a612ff7',
            # 'taskState': 'running', 'taskResult': '', 'code': 0,
            # 'message': 'running job 1 of 1'}
            while True:
                time.sleep(5)
                status = cli.Task.getStatus(taskID=task_id)
                log.debug("Task status: %s", status)
                if status["taskState"] != "running":
                    break

            log.debug("Clearing task %s", task_id)
            cli.Task.clear(taskID=task_id)

            if status["code"] != 0:
                raise RuntimeError("Task failed: %s", status["message"])

            pool_info = cli.StoragePool.getInfo(storagepoolID=args.pool_id)
            if pool_info['info']['master_ver'] != master_ver:
                raise RuntimeError(
                    "Unexpected master_ver value: expecting: {} actual: {}"
                    .format(master_ver, pool_info['info']['master_ver']))

            if pool_info['info']['master_uuid'] != new_master:
                raise RuntimeError(
                    "Unexpected master_uuid value: expecting: {} actual: {}"
                    .format(new_master, pool_info['info']['master_uuid']))

            new_master_info = cli.StorageDomain.getInfo(
                storagedomainID=new_master)
            if new_master_info['role'] != "Master":
                raise RuntimeError(
                    "Role for new master domain didn't change to Master")

            old_master_info = cli.StorageDomain.getInfo(
                storagedomainID=old_master)
            if old_master_info['role'] != "Regular":
                raise RuntimeError(
                    "Role for old master domain didn't change to Regular")

            log.info("Master switched successfully")
            new_master, old_master = old_master, new_master
            master_ver += 1


def parse_args():
    p = argparse.ArgumentParser('Switch master domain from the command line')

    p.add_argument(
        "--pool-id",
        type=str,
        required=True,
        help="The storage pool associated with the storage domains")

    p.add_argument(
        "--old-master",
        type=str,
        required=True,
        help="The current master storage domain UUID")

    p.add_argument(
        "--new-master",
        type=str,
        required=True,
        help="The new master storage domain UUID")

    p.add_argument(
        "--master-version",
        type=int,
        help="The new master's version (default is current version + 1)")

    p.add_argument(
        "--iterations",
        default=1,
        type=int,
        help="The iterations number for switching the master (default is 1)")

    return p.parse_args()


if __name__ == '__main__':
    main()
