"""Install the opt-in systemd retention timer after preview and worker upgrade."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile

from retention_policy import validate


def units(config_path, schedule):
    release = Path(__file__).resolve().parent
    for path in (release, config_path):
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)):
            raise ValueError('Unsupported deployment path')
    if not isinstance(schedule, str) or not re.fullmatch(r'[A-Za-z0-9*:,./ +_-]+', schedule):
        raise ValueError('Invalid OnCalendar schedule')
    service = '''[Unit]
Description=Enqueue auction snapshot retention
After=wow-auction-sync-worker.service
Requires=wow-auction-sync-worker.service
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/bin/python3 {release}/retention_schedule.py --config {config} --apply
'''.format(release=release, config=config_path)
    timer = '''[Unit]
Description=Periodic auction snapshot retention
[Timer]
OnCalendar={schedule}
Persistent=true
AccuracySec=1min
Unit=wow-auction-retention.service
[Install]
WantedBy=timers.target
'''.format(schedule=schedule)
    return service, timer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    path = args.config.resolve(strict=True)
    config = json.loads(path.read_text())
    validate(config.get('policy', {}))
    schedule = config.get('on_calendar', 'hourly')
    service, timer = units(path, schedule)
    # systemd 219 has no `calendar` command or RandomizedDelaySec.
    with tempfile.TemporaryDirectory(prefix='auction-retention-units-') as folder:
        paths = []
        for suffix, content in (('service', service), ('timer', timer)):
            candidate = Path(folder)/('wow-auction-retention.'+suffix)
            candidate.write_text(content)
            paths.append(str(candidate))
        subprocess.check_call(['systemd-analyze', 'verify'] + paths)
    for suffix, content in (('service', service), ('timer', timer)):
        Path('/etc/systemd/system/wow-auction-retention.'+suffix).write_text(content)
    subprocess.check_call(['systemctl', 'daemon-reload'])
    subprocess.check_call(['systemctl', 'enable', '--now', 'wow-auction-retention.timer'])
    subprocess.check_call(['systemctl', 'restart', 'wow-auction-retention.timer'])


if __name__ == '__main__':
    main()
