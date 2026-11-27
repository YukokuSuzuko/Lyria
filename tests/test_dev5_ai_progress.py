from lyricsync.api_client import transport_progress,batch_progress

def test_heartbeat_or_reasoning_does_not_claim_content():
    for values in ({'heartbeat_count':4},{'reasoning_events':3}):
        stage=transport_progress({'phase':'read_stream','received_bytes':120,**values})
        result=batch_progress(stage,1,4,2,9,'diagnostics '+str(stage))
        assert '2 / 9' in result.summary and '等待回复内容' in result.summary
        assert '120' in str(result) and '字节' not in result.summary
        assert '完成' not in result.summary

def test_actual_content_is_receiving_not_complete():
    stage=transport_progress({'phase':'read_stream','content_chars':10,'received_bytes':250})
    result=batch_progress(stage,1,4,3,7,str(stage))
    assert '正在接收回复内容' in result.summary and '3 / 7' in result.summary
    assert '完成' not in result.summary

def test_state_summaries_do_not_hide_diagnostic_detail():
    for stage,label in [('requesting','等待回复'),('validating','检查回复内容'),('cached','读取已保存结果'),('read_timeout','本批请求未完成')]:
        result=batch_progress(stage,2,4,1,6,'原始重试详情')
        assert label in result.summary and str(result)=='原始重试详情'


def test_summary_survives_copy_without_changing_string_contract():
    from copy import deepcopy
    value=transport_progress({'phase':'read_stream','content_chars':1})
    copy=deepcopy(value)
    assert isinstance(copy,str) and str(copy)==str(value) and copy.summary==value.summary


def test_initial_batch_progress_separates_summary_from_accounting(tmp_path):
    import json
    from test_api_phase5_windows import fixture,empty
    from lyricsync import api_alignment
    from lyricsync.storage import Cache
    project,index=fixture(1);cache=Cache(tmp_path/'progress.sqlite');events=[]
    try:
        api_alignment.request_plan(project,index,cache,'fake-test-key',
            transport=lambda *_:(json.dumps(empty()),{}),
            progress=lambda fraction,message:events.append(message))
        assert events[0].summary=='准备核对，共 1 批'
        assert '预算' in str(events[0]) and '预算' not in events[0].summary
    finally:cache.close()
