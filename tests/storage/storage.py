"""
userstorage configuration
"""

from userstorage import File, Mount, LoopDevice

GiB = 1024**3

BASE_DIR = "/var/tmp/vdsm-storage"

BACKENDS = {

    "file-512":
        File(
            Mount(
                LoopDevice(
                    base_dir=BASE_DIR,
                    name="file-512",
                    size=GiB,
                    sector_size=512))),
    "file-4k":
        File(
            Mount(
                LoopDevice(
                    base_dir=BASE_DIR,
                    name="file-4k",
                    size=GiB,
                    sector_size=4096))),
    "mount-512":
        Mount(
            LoopDevice(
                base_dir=BASE_DIR,
                name="mount-512",
                size=GiB,
                sector_size=512)),
    "mount-4k":
        Mount(
            LoopDevice(
                base_dir=BASE_DIR,
                name="mount-4k",
                size=GiB,
                sector_size=4096)),

}
