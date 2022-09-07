# SPDX-FileCopyrightText: Red Hat, Inc.
# SPDX-License-Identifier: GPL-2.0-or-later

echo 'Please run this script from vdsm main folder'
echo '============================================'

./build-aux/make-dist with_hooks 1 with_vhostmd 1
