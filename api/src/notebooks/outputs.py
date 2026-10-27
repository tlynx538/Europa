"""Reduce correlated Jupyter IOPub messages to standard nbformat outputs."""
import copy
import nbformat


def notebook_result(result):
    outputs, displays, updates = [], {}, {}
    waiting = False
    count = result['reply'].get('execution_count')
    for message in result['messages']:
        if message.get('parent_header', {}).get('msg_id') != result['execution_id']:
            continue
        if message.get('channel') != 'iopub': continue
        kind, content = message['header']['msg_type'], message['content']
        if kind == 'execute_input': count = content.get('execution_count', count)
        if kind == 'clear_output':
            waiting = bool(content.get('wait'))
            if not waiting: outputs.clear(); displays.clear()
            continue
        if kind not in {'stream','display_data','execute_result','update_display_data','error'}: continue
        if waiting:
            outputs.clear(); displays.clear(); waiting = False
        if kind == 'stream':
            outputs.append(nbformat.v4.new_output('stream', name=content['name'], text=content['text']))
        elif kind in {'display_data', 'execute_result'}:
            fields = {'data': copy.deepcopy(content['data']), 'metadata': copy.deepcopy(content.get('metadata', {}))}
            if kind == 'execute_result': fields['execution_count'] = content['execution_count']
            outputs.append(nbformat.v4.new_output(kind, **fields))
            display = content.get('transient', {}).get('display_id')
            if display: displays.setdefault(display, []).append(len(outputs) - 1)
        elif kind == 'update_display_data':
            display = content.get('transient', {}).get('display_id')
            if display:
                update = {'data': copy.deepcopy(content['data']), 'metadata': copy.deepcopy(content.get('metadata', {}))}
                updates[display] = update
                for index in displays.get(display, []): outputs[index].update(copy.deepcopy(update))
        elif kind == 'error':
            outputs.append(nbformat.v4.new_output('error', ename=content['ename'], evalue=content['evalue'], traceback=content['traceback']))
    return {'status': result['reply']['status'], 'execution_count': count, 'outputs': outputs,
            'display_map': displays, 'display_updates': updates, 'protocol_execution_id': result['execution_id'],
            'kernel_session': next((m['header']['session'] for m in result['messages']
                if m.get('parent_header', {}).get('msg_id') == result['execution_id'] and m.get('header', {}).get('session')), None)}
