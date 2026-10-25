"""Thirty-minute coordinator: model status -> Luna review -> fresh gated outbox."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import socket

from dc_runtime import utc
import high_probability as high
from publisher import flush
from sports_review import MODEL,EFFORT,validate_packet,validate_decisions
from store import Store


def model_statuses(config,preview):
    registry=json.loads(Path(config['sports_model_registry']).read_text(encoding='utf-8'))
    result=[]
    for row in registry['models']:
        deployed=all(Path(p).is_file() for p in row['required_files'])
        active=row['key']=='epl_dc_1x2'
        blocked=[] if active else list(row['blocked_reasons'])
        state=('ready' if preview['status']=='ready_no_qualifying_match' else 'data_unavailable') if active else 'research_only'
        if not deployed:state='source_missing';blocked.append('source_file_missing')
        if active and preview['status']!='ready_no_qualifying_match':blocked.append(preview['status'])
        result.append({'key':row['key'],'state':state,'blocked_reasons':blocked,'source_deployed':deployed})
    return result


def request_review(packet,path='/run/dnsgon-sports-inference.sock'):
    validate_packet(packet)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(85);connection.connect(path)
        connection.sendall(json.dumps(packet,ensure_ascii=False).encode()+b'\n')
        with connection.makefile('rb') as source:raw=source.readline(20002)
    if len(raw)>20001 or not raw.endswith(b'\n'):raise ValueError('invalid_review_response')
    value=json.loads(raw)
    if value.get('ok') is not True or value.get('model')!=MODEL or value.get('effort')!=EFFORT:
        raise RuntimeError('luna_review_unavailable')
    return validate_decisions(value['review'],packet)


def cycle(config,source,target,*,clock=None,reviewer=request_review,statuses=model_statuses):
    clock=clock or (lambda:datetime.now(timezone.utc));now=utc(clock())
    rows,preview=high.collect(config,source,now)
    locked={p['id'] for p in target.predictions()}
    models=statuses(config,preview)
    adapter_ready=any(r['key']=='epl_dc_1x2' and r['state']=='ready' and r['source_deployed'] for r in models)
    new=[r for r in rows if r['id'] not in locked][:12] if adapter_ready else []
    packet=validate_packet({'schema':1,'checked_at':now.isoformat(),'models':models,
                           'candidates':[{'id':r['id'],'digest':high.candidate_digest(r),
                                          'evidence':high.evidence(r),'cautions':high.CAUTIONS} for r in new]})
    try:
        decisions=validate_decisions(reviewer(packet),packet)
        approved={r['id']:r for r in decisions['decisions'] if r['allow']}
        review_status='ok'
    except (OSError,ValueError,RuntimeError,KeyError,TypeError,TimeoutError):
        decisions={'cycle_note':'data_unavailable','decisions':[]};approved={};review_status='unavailable'
    def approve(row):
        value=approved.get(row['id'])
        if not value or value['digest']!=high.candidate_digest(row):return None
        return {k:value[k] for k in ('reason_ids','caution_ids')}|{'model':MODEL,'effort':EFFORT,'reviewed_at':utc(clock()).isoformat()}
    # Recheck current source, model, quote age and kickoff AFTER inference.
    result=high.scan(config,source,target,utc(clock()),reviewer=approve)
    detail={'model':MODEL,'effort':EFFORT,'interval_minutes':30,'review_status':review_status,
            'models':packet['models'],'new_candidates':len(new),'reviewed':len(decisions['decisions']),
            'ai_approved':len(approved),'pipeline':result}
    target.snapshot('agent:latest',detail,utc(clock()))
    target.record_run('agent_30m_'+review_status,detail,utc(clock()))
    if review_status=='ok':
        available=sum(r['state']=='ready' for r in packet['models'])
        target.enqueue('agent:setup:30m:v1',
            '**DNS GỌN · AGENT LUNA ĐÃ BẬT**\n\n'
            'VPS tự rà soát mỗi **30 phút**, kể cả khi PC tắt.\n'
            f'Model AI: **Luna · Extra high**. Lượt đầu đã kiểm tra {len(packet["models"])} nhóm model; '
            f'{available} nhóm có thể xét tín hiệu.\n\n'
            '**Điều kiện phát:** xác suất từ model độc lập ≥75%, EV theo model ≥+3%, '
            'đúng lịch trận, giá còn mới, đủ lịch sử; Luna rà soát dữ kiện trước khi gửi.\n'
            'Thông báo có lựa chọn, xác suất, odds, lý do chọn và điểm cần lưu ý.\n\n'
            '**Hiện bật:** EPL 1X2. LoL/BO3/BO5 đã có source nghiên cứu trên VPS; '
            'LoL, Dota 2, CS2, NBA, tennis, bóng chuyền và EPL chấp/tài xỉu còn thiếu feed hoặc kiểm định riêng nên chưa phát tín hiệu.\n\n'
            f'Lượt này: {len(new)} cửa mới qua bộ lọc cứng, {result["qualified_this_run"]} thông báo được xếp gửi. '
            'Không có cửa đủ điều kiện thì im lặng; không tự đặt cược, không hứa chắc thắng.')
    return detail


def main():
    import fcntl
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='/etc/dnsgon-sports.json')
    parser.add_argument('--status',action='store_true');args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if config.get('high_probability_dispatch_mode')!='agent_30m':raise ValueError('agent_dispatch_not_enabled')
    with Path(config['high_probability_database']).with_suffix('.agent.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        source=Store(config['database']);target=Store(config['high_probability_database'])
        try:
            if args.status:
                print(json.dumps(target.latest_snapshot('agent:latest'),ensure_ascii=True));return
            target.recover_sending()
            result=cycle(config,source,target)
            result['messages_sent']=flush(target,config['telegram_token'],config['high_probability_channel_id'],datetime.now(timezone.utc).isoformat())
            print(json.dumps(result,ensure_ascii=True))
        finally:source.close();target.close()


if __name__=='__main__':main()
