from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from power_agent.tools import AGENT_TOOLS


def payload(result):
    document = result.model_dump(by_alias=True)
    if document.get('structuredContent') is not None:
        return document['structuredContent']
    return json.loads(next(block['text'] for block in document['content'] if block['type'] == 'text'))


def test_real_stdio_handshake_schema_errors_and_complete_job(tmp_path):
    async def scenario():
        root = Path(__file__).resolve().parents[2]
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'power_agent.mcp_server'], cwd=str(root),
            env={'POWER_DESIGN_WORKSPACE': str(tmp_path), 'MPLCONFIGDIR': str(tmp_path/'mpl'),
             'XDG_CACHE_HOME': str(tmp_path/'cache'), 'PYTHONDONTWRITEBYTECODE': '1'})
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write, read_timeout_seconds=60) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == set(AGENT_TOOLS)
            analyze = next(tool for tool in listed.tools if tool.name == 'llc_analyze')
            schema = analyze.model_dump(by_alias=True)['inputSchema']
            assert 'pout_w' in schema['$defs']['LLCSpecInput']['properties']
            defaults = await session.call_tool('llc_defaults', {})
            assert payload(defaults)['spec']['vbus_nom_v'] == 400
            bad = await session.call_tool('llc_create_design', {'spec': {'pout_w': True}})
            assert bad.model_dump(by_alias=True)['isError'] is True
            created = await session.call_tool('llc_create_design', {'spec': {}})
            assert not created.model_dump(by_alias=True).get('isError')
            design = payload(created)
            submitted = await session.call_tool('llc_check_corners', {
                'design_id': design['design_id'],
                'request': {'vbus_values_v': [400.0], 'load_fractions': [1.0]}})
            job_id = payload(submitted)['job_id']
            for _ in range(1000):
                status = payload(await session.call_tool('job_get', {'job_id': job_id}))
                if status['state'] != 'RUNNING':
                    break
                await asyncio.sleep(0.05)
            assert status['state'] == 'COMPLETED', status
            assert status['design_status'] == 'PASS'
            exported = payload(await session.call_tool('design_export', {'job_id': job_id}))
            assert Path(exported['path']).is_file()
            report = json.loads(Path(exported['path']).read_text())
            assert report['hardware_status'] == 'NOT_RUN'
            resources = await session.list_resources()
            assert any(str(resource.uri) == 'power-design://capabilities' for resource in resources.resources)
    asyncio.run(scenario())
