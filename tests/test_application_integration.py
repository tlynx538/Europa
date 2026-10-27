"""Real FastAPI lifespan restart with trusted local notebook execution."""
import asyncio
import httpx
import pytest

from main import create_app
from runtime.local import LocalRuntimeProvider
from test_application import op

pytestmark = pytest.mark.integration


async def test_fastapi_restart_persists_application_and_execution(tmp_path):
    app = create_app(runtime_provider=LocalRuntimeProvider())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://europa') as http:
            async def post(path, body):
                response = await http.post('/api'+path,json=body)
                assert response.status_code in {200,201,202},response.text
                return response.json()
            p=await post('/projects',{'operation_id':op(),'title':'Restart proof'})
            doc=await post('/notebooks',{'title':'Durable'})
            nid=doc['metadata']['europa']['notebook_id']
            doc=await post(f'/notebooks/{nid}/cells',{'revision':1,'source':'print(6*7)'})
            cid=doc['cells'][0]['id']
            p=await post(f"/projects/{p['id']}/notebooks",{'operation_id':op(),'revision':1,'notebook_id':nid})
            c=await post('/conversations',{'operation_id':op(),'project_id':p['id'],'notebook_id':nid})
            msg=await post(f"/conversations/{c['id']}/messages",{'operation_id':op(),'content':'Compute the result','cell_id':cid})
            r=await post('/roadmaps',{'operation_id':op(),'project_id':p['id'],'notebook_id':nid,'objective':'Compute','tasks':[
                {'id':'compute','title':'Compute answer','acceptance_criteria':['Result is 42'],'links':{'notebook_id':nid,'cell_id':cid}}]})
            comment=await post(f'/notebooks/{nid}/comments',{'operation_id':op(),'project_id':p['id'],'notebook_id':nid,'cell_id':cid,'content':'Verify the result'})
            event=await post(f"/projects/{p['id']}/activities",{'operation_id':op(),'project_id':p['id'],'summary':'Requested calculation'})
            receipt=await post(f'/notebooks/{nid}/cells/{cid}/execute',{'revision':2})
            await asyncio.wait_for(asyncio.gather(*tuple(app.state.notebook_service.tasks.values())),40)
            saved=app.state.notebook_store.execution(nid,receipt['execution_id'])
            assert saved['state']=='applied' and saved['result']['status']=='ok'
    restarted=create_app(runtime_provider=LocalRuntimeProvider())
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted),base_url='http://europa') as http:
            for path,expected in [(f"/projects/{p['id']}",p['id']), (f"/conversations/{c['id']}",c['id']), (f"/roadmaps/{r['id']}",r['id'])]:
                response=await http.get('/api'+path)
                assert response.status_code==200 and response.json()['id']==expected
            assert (await http.get(f"/api/conversations/{c['id']}/messages")).json()['items'][0]['id']==msg['id']
            assert (await http.get(f'/api/notebooks/{nid}/comments')).json()['items'][0]['id']==comment['id']
            assert (await http.get(f"/api/projects/{p['id']}/activities")).json()['items'][0]['id']==event['id']
            doc=(await http.get(f'/api/notebooks/{nid}')).json()
            assert doc['cells'][0]['outputs'][0]['text']=='42\n'
            # A file/metadata reopen does not provision a runtime for the notebook.
            assert nid not in restarted.state.supervisor.notebooks
