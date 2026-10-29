"""Install the isolated ingress and worker on the existing production host (Python 3.6)."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request


def command(*args):
    subprocess.check_call(list(args))


def install(image,token_file):
    if not re.fullmatch('sha256:[a-f0-9]{64}',image): raise ValueError('Pin an immutable local image ID')
    release=Path(__file__).resolve().parent
    root=Path('/opt/wow-auction/data/sync')
    for path in (release,token_file):
        if not re.fullmatch('/[A-Za-z0-9_./-]+',str(path)): raise ValueError('Unsupported deployment path')
    if not token_file.is_file() or len(token_file.read_text().strip())<32: raise ValueError('Missing private sync token')
    if Path('/opt/wow-auction/data/.dataset-switch.lock').exists(): raise ValueError('A legacy publication is active')
    root.mkdir(parents=True,exist_ok=True)
    for folder in (root,root/'jobs',root/'receipts'):
        folder.mkdir(exist_ok=True);os.chown(str(folder),65534,65534);os.chmod(str(folder),0o700)
    os.chown(str(token_file),65534,65534);os.chmod(str(token_file),0o400)
    config=json.loads((release/'sync.example.json').read_text())
    config['import_image']=image
    config_file=release/'sync-worker.local.json'
    config_file.write_text(json.dumps(config,indent=2)+'\n');os.chmod(str(config_file),0o600)
    executable=subprocess.check_output(['which','docker'],universal_newlines=True).strip()
    ingress='''[Unit]
Description=Auction snapshot sync ingress
After=docker.service
Requires=docker.service
[Service]
Restart=always
RestartSec=3
ExecStartPre=-{docker} rm -f wow-auction-sync-ingress
ExecStart={docker} run --rm --name wow-auction-sync-ingress --memory 128m --memory-swap 128m --cpus 0.5 --pids-limit 64 --read-only --cap-drop ALL --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,size=32m --log-opt max-size=10m --log-opt max-file=3 -p 127.0.0.1:8182:8001 -v {root}:/sync:rw -v {token}:/run/secrets/sync-token:ro -e SYNC_ROOT=/sync -e SYNC_TOKEN_FILE=/run/secrets/sync-token -e SYNC_SWITCH_LOCK=/sync/dataset-switch.lock {image}
ExecStop={docker} stop -t 10 wow-auction-sync-ingress
TimeoutStopSec=20
[Install]
WantedBy=multi-user.target
'''.format(docker=executable,root=root,token=token_file,image=image)
    worker='''[Unit]
Description=Auction snapshot import and publication worker
After=docker.service wow-auction-sync-ingress.service
Requires=docker.service
[Service]
Type=simple
Restart=always
RestartSec=5
UMask=0077
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 {release}/sync_worker.py --config {config}
TimeoutStopSec=20
[Install]
WantedBy=multi-user.target
'''.format(release=release,config=config_file)
    Path('/etc/systemd/system/wow-auction-sync-ingress.service').write_text(ingress)
    Path('/etc/systemd/system/wow-auction-sync-worker.service').write_text(worker)
    api=Path('/etc/nginx/wow-auction-api.conf');previous=api.read_bytes()
    if b'location ^~ /api/sync/' in previous: raise ValueError('Sync Nginx route already exists; update services explicitly')
    upstream=Path('/etc/nginx/conf.d/wow-auction-sync-upstream.conf')
    if upstream.exists(): raise ValueError('Sync upstream already exists')
    (release/'previous-api.conf').write_bytes(previous)
    upstream.write_text('upstream wow_auction_sync_ingress { server 127.0.0.1:8182; }\n')
    route=b'''
# snapshot-sync-ingress v1
location ^~ /api/sync/ {
    client_max_body_size 128m;
    client_body_timeout 60s;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_pass http://wow_auction_sync_ingress;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_read_timeout 620s;
    proxy_send_timeout 620s;
}
'''
    try:
        command('systemctl','daemon-reload')
        command('systemctl','enable','--now','wow-auction-sync-ingress','wow-auction-sync-worker')
        import time
        ready=False
        for attempt in range(20):
            try: urllib.request.urlopen('http://127.0.0.1:8182/api/sync/jobs/'+'0'*32,timeout=2)
            except urllib.error.HTTPError as exc:
                if exc.code==401: ready=True;break
            except OSError: pass
            time.sleep(1)
        if not ready: raise ValueError('Sync ingress failed to start')
        api.write_bytes(previous+route)
        command('nginx','-t');command('nginx','-s','reload')
        try: urllib.request.urlopen('https://wowplayer.fun/api/sync/jobs/'+'0'*32,timeout=15)
        except urllib.error.HTTPError as exc:
            if exc.code!=401: raise
        else: raise ValueError('Unauthenticated sync request was not rejected')
    except Exception:
        api.write_bytes(previous);upstream.unlink()
        command('nginx','-t');command('nginx','-s','reload')
        subprocess.call(['systemctl','disable','--now','wow-auction-sync-worker','wow-auction-sync-ingress'])
        raise
    print(json.dumps({'status':'installed','image':image,'endpoint':'https://wowplayer.fun/api/sync/jobs'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--image',required=True);parser.add_argument('--token-file',type=Path,required=True)
    args=parser.parse_args();install(args.image,args.token_file)
