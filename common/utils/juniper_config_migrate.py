#!/usr/bin/env python3
#
# Juniper config_db.json migration fix-ups
#
# Copyright (C) 2026 Juniper Networks
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# Run from the config-setup post-migration hook, after the previous image's
# config_db.json has been copied into /etc/sonic and before config-setup loads
# it. A config saved by an older image can fail YANG validation, which aborts
# config-setup and leaves only database/gnmi/sysmgr running:
#
#   * MGMT_INTERFACE names a port that has no MGMT_PORT entry (leafref error).
#
# This script never fails the boot: on any problem it leaves the file as is.

import argparse
import json
import os
import re
import sys
import tempfile

DEFAULT_CONFIG_DB = "/etc/sonic/config_db.json"
# Same pattern as the name leaf of sonic-mgmt_port.yang.
MGMT_PORT_NAME = re.compile(r"eth([1-3][0-9]{3}|[1-9][0-9]{2}|[1-9][0-9]|[0-9])")


def add_missing_mgmt_ports(config):
    """Add MGMT_PORT entries for the ports MGMT_INTERFACE refers to."""
    interfaces = config.get("MGMT_INTERFACE")
    if not isinstance(interfaces, dict):
        return []

    ports = config.get("MGMT_PORT")
    existing = ports if isinstance(ports, dict) else {}
    added = []
    for key in interfaces:
        name = str(key).split("|", 1)[0]
        if name in existing or name in added or not MGMT_PORT_NAME.fullmatch(name):
            continue
        added.append(name)

    if added:
        if not isinstance(ports, dict):
            ports = config["MGMT_PORT"] = {}
        for name in added:
            ports[name] = {"admin_status": "up"}
    return added


def write_atomically(path, config):
    """Replace path with config, keeping its mode and ownership."""
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=4)
            f.write("\n")
        os.chmod(tmp, st.st_mode & 0o7777)
        try:
            os.chown(tmp, st.st_uid, st.st_gid)
        except PermissionError:
            pass                        # not root: nothing to preserve
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def migrate(path):
    """Apply the fix-ups to path; return the list of changes made."""
    with open(path) as f:
        original = f.read()
    config = json.loads(original)
    if not isinstance(config, dict):
        return []

    changes = ["added MGMT_PORT|" + n for n in add_missing_mgmt_ports(config)]
    if not changes:
        return []

    backup = path + ".pre-juniper-migration"
    if not os.path.exists(backup):
        with open(backup, "w") as f:
            f.write(original)
    write_atomically(path, config)
    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1] if __doc__ else "")
    parser.add_argument("config_db", nargs="?", default=DEFAULT_CONFIG_DB)
    args = parser.parse_args(argv)

    if not os.path.isfile(args.config_db):
        return 0
    try:
        changes = migrate(args.config_db)
    except (OSError, ValueError) as e:
        print("juniper-config-migrate: leaving {} unchanged: {}".format(args.config_db, e))
        return 0

    for change in changes:
        print("juniper-config-migrate: {} in {}".format(change, args.config_db))
    return 0


if __name__ == "__main__":
    sys.exit(main())
