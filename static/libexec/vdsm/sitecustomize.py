from vdsm.config import config

if config.getboolean('devel', 'coverage_enable'):
    import coverage  # pylint: disable=import-error
    coverage.process_startup()
