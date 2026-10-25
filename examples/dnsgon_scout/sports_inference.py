"""Socket-activated Luna; no Telegram/financial credentials and no tools."""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile

from sports_review import EFFORT,MODEL,ROLE,SCHEMA,validate_packet,validate_decisions

HOME=Path('/var/lib/codex-coach')


def review(packet,run=subprocess.run,home=HOME):
    validate_packet(packet)
    env={'HOME':str(home),'CODEX_HOME':str(home/'.codex'),'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    with tempfile.TemporaryDirectory(prefix='sports-review-',dir=home) as directory:
        output=Path(directory)/'reply.json';schema=Path(directory)/'schema.json'
        schema.write_text(json.dumps(SCHEMA),encoding='utf-8')
        command=['/opt/codex-coach/codex','exec','--ignore-user-config','--ephemeral','--sandbox','read-only','--skip-git-repo-check',
                 '--model',MODEL,'-c','model_reasoning_effort='+json.dumps(EFFORT),
                 '-c','approval_policy="never"','-c','features.shell_tool=false',
                 '-c','features.multi_agent=false','-c','features.apps=false','-c','web_search="disabled"',
                 '-c','developer_instructions='+json.dumps(ROLE),
                 '--output-schema',str(schema),'-o',str(output),'-']
        try:
            result=run(command,input=json.dumps(packet,ensure_ascii=False).encode(),stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE,env=env,cwd=home/'sports-workspace',timeout=75,check=False)
        except (subprocess.TimeoutExpired,OSError):
            raise RuntimeError('luna_unavailable') from None
        if result.returncode or not output.exists():raise RuntimeError('luna_inference_failed')
        header=result.stderr.decode('utf-8','replace')
        for label,expected in (('model',MODEL),('reasoning effort',EFFORT)):
            match=re.search('^'+label+r':\s*(\S+)\s*$',header,re.M)
            if not match or match[1]!=expected:raise RuntimeError('luna_model_mismatch')
        with output.open('rb') as source:raw=source.read(16001)
        if len(raw)>16000:raise ValueError('luna_output_large')
        return validate_decisions(json.loads(raw),packet)


def serve(connection,allowed_uid,respond=review):
    peer=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    if peer[1]!=allowed_uid:return
    connection.settimeout(5)
    try:
        with connection.makefile('rb') as source:raw=source.readline(32002)
        if len(raw)>32001 or not raw.endswith(b'\n'):raise ValueError('invalid_packet')
        packet=validate_packet(json.loads(raw))
        result={'ok':True,'model':MODEL,'effort':EFFORT,'review':respond(packet)}
    except Exception:
        result={'ok':False,'error':'luna_review_unavailable'}
    connection.sendall(json.dumps(result,ensure_ascii=False).encode()+b'\n')


if __name__=='__main__':
    import pwd
    if (os.geteuid()!=pwd.getpwnam('codex-coach').pw_uid
            or os.environ.get('LISTEN_PID')!=str(os.getpid()) or os.environ.get('LISTEN_FDS')!='1'):
        raise SystemExit('socket_activation_required')
    listener=socket.socket(fileno=3)
    connection,_=listener.accept()
    with connection:serve(connection,pwd.getpwnam('sports-scout').pw_uid)
