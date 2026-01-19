#!/usr/bin/env python3
"""
服务部署客户端代理
运行在服务器上，提供HTTP接口用于部署、重启、关闭服务
支持直接启动推理服务进行模型推理
"""

from flask import Flask, request, jsonify, Response, stream_with_context
import subprocess
import threading
import os
import signal
import time
import shlex
import re
import socket
from datetime import datetime
import json
import argparse
import queue
import uuid
import contextlib
import requests

# 推理服务相关导入（可选，如果模块不存在会失败）
try:
    from jiuge import JiugeForCauslLM
    from jiuge_awq import JiugeAWQForCausalLM
    from libinfinicore_infer import DeviceType
    from infer_task import InferTask
    from kvcache_pool import KVCachePool
    INFERENCE_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] 推理服务模块未找到: {e}")
    print("[WARNING] 将回退到子进程模式启动服务")
    INFERENCE_AVAILABLE = False

# FastAPI相关导入
try:
    from fastapi import FastAPI as FastAPIApp, Request
    from fastapi.responses import StreamingResponse as FastAPIStreamingResponse, JSONResponse as FastAPIJSONResponse
    import uvicorn
    import janus
    FASTAPI_AVAILABLE = True
except ImportError:
    print("[WARNING] FastAPI/uvicorn未安装，推理服务功能将不可用")
    FASTAPI_AVAILABLE = False

def get_env_with_path():
    """获取包含PATH的环境变量字典"""
    env = os.environ.copy()
    # 确保PATH包含常见的系统路径
    common_paths = [
        '/usr/local/sbin',
        '/usr/local/bin',
        '/usr/sbin',
        '/usr/bin',
        '/sbin',
        '/bin'
    ]
    current_path = env.get('PATH', '')
    for path in common_paths:
        if path not in current_path:
            current_path = f"{path}:{current_path}"
    env['PATH'] = current_path
    return env

app = Flask(__name__)

# 设备类型映射
if INFERENCE_AVAILABLE:
    DEVICE_TYPE_MAP = {
        "cpu": DeviceType.DEVICE_TYPE_CPU,
        "nvidia": DeviceType.DEVICE_TYPE_NVIDIA,
        "qy": DeviceType.DEVICE_TYPE_QY,
        "cambricon": DeviceType.DEVICE_TYPE_CAMBRICON,
        "ascend": DeviceType.DEVICE_TYPE_ASCEND,
        "metax": DeviceType.DEVICE_TYPE_METAX,
        "moore": DeviceType.DEVICE_TYPE_MOORE,
        "iluvatar": DeviceType.DEVICE_TYPE_ILUVATAR,
        "kunlun": DeviceType.DEVICE_TYPE_KUNLUN,
        "hygon": DeviceType.DEVICE_TYPE_HYGON,
    }
else:
    DEVICE_TYPE_MAP = {}

# 存储服务进程信息
# {service_id: {
#     'process': subprocess.Popen or None,
#     'inference_app': FastAPI app or None,
#     'uvicorn_server': uvicorn.Server or None,
#     'inference_thread': Thread or None,
#     'command': str,
#     'config': dict,  # 推理服务配置
#     'status': str,
#     'start_time': datetime,
#     'port': int  # 推理服务端口
# }}
services = {}

# 默认端口
DEFAULT_PORT = 8888  # service_agent 监听端口
DEFAULT_INFERENCE_PORT = 8889  # 推理服务端口（避免与service_agent冲突）


def find_free_port(start_port: int, host: str = "0.0.0.0", max_tries: int = 50) -> int:
    """从 start_port 开始扫描可用端口，避免 'address already in use'。"""
    port = int(start_port)
    for _ in range(max_tries):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return port
        except OSError:
            port += 1
        finally:
            try:
                s.close()
            except Exception:
                pass
    raise RuntimeError(f"无法找到可用端口（从 {start_port} 起扫描 {max_tries} 个）")


def parse_deploy_command(command):
    """解析部署命令，提取推理服务参数"""
    # 尝试解析命令行参数
    # 格式可能是: python script.py --model-path /path --dev cpu --ndev 1 --max-batch 3
    # 或者只是参数字符串: --model-path /path --dev cpu
    
    # 先尝试使用shlex解析
    try:
        parts = shlex.split(command)
    except:
        # 如果解析失败，尝试简单分割
        parts = command.split()
    
    # 查找python脚本位置（跳过）
    args_dict = {}
    i = 0
    while i < len(parts):
        part = parts[i]
        if part.startswith('--'):
            key = part[2:].replace('-', '_')
            if i + 1 < len(parts) and not parts[i + 1].startswith('--'):
                args_dict[key] = parts[i + 1]
                i += 2
            else:
                # 布尔标志
                args_dict[key] = True
                i += 1
        elif part == 'python' or part.endswith('.py'):
            # 跳过python和脚本名
            i += 1
        else:
            i += 1
    
    return args_dict


def parse_config_from_command(command):
    """从命令中解析配置参数"""
    config = parse_deploy_command(command)
    
    # 设置默认值
    result = {
        'model_path': config.get('model_path') or config.get('model-path', ''),
        'dev': config.get('dev', 'cpu'),
        'ndev': int(config.get('ndev', 1)),
        'max_batch': int(config.get('max_batch') or config.get('max-batch', 3)),
        'max_tokens': int(config.get('max_tokens') or config.get('max-tokens')) if config.get('max_tokens') or config.get('max-tokens') else None,
        'awq': config.get('awq', False),
        # port 可选：如果命令未指定，则后续动态分配，避免端口冲突
        'port': int(config.get('port')) if config.get('port') else None
    }
    
    return result


def chunk_json(id_, content=None, role=None, finish_reason=None):
    """生成SSE格式的JSON块"""
    delta = {}
    if content:
        delta["content"] = content
    if role:
        delta["role"] = role
    return {
        "id": id_,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "jiuge",
        "system_fingerprint": None,
        "choices": [
            {
                "index": 0,
                "text": content,
                "delta": delta,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
    }


# 推理任务的异步包装类
if INFERENCE_AVAILABLE and FASTAPI_AVAILABLE:
    class AsyncInferTask(InferTask):
        def __init__(self, id, tokens, max_tokens, temperature, topk, topp, end_tokens):
            super().__init__(id, tokens, max_tokens, temperature, topk, topp, end_tokens)
            self.output_queue = janus.Queue()
            print(f"[INFO] Create InferTask {self.id}")

        def output(self, out_token):
            self.next(out_token)
            self.output_queue.sync_q.put(out_token)


def worker_loop(app_state):
    """推理工作循环"""
    MAX_BATCH = app_state.config['max_batch']
    while not app_state.shutdown_event.is_set():
        try:
            task = app_state.request_queue.sync_q.get(timeout=0.01)
        except queue.Empty:
            continue

        if task is None:
            return

        batch = [task]
        while len(batch) < MAX_BATCH:
            try:
                req = app_state.request_queue.sync_q.get_nowait()
                if req is not None:
                    batch.append(req)
            except queue.Empty:
                break
        
        output_tokens = app_state.model.batch_infer_one_round(batch)
        for task, token in zip(batch, output_tokens):
            task.output(token)
            if task.finish_reason is None:
                app_state.request_queue.sync_q.put(task)
            else:
                print(f"[INFO] Task {task.id} finished infer.")
                app_state.kv_cache_pool.release_sync(task)


def build_inference_app(service_id, config):
    """构建推理服务的FastAPI应用"""
    if not INFERENCE_AVAILABLE or not FASTAPI_AVAILABLE:
        raise RuntimeError("推理服务模块不可用")
    
    model_path = config['model_path']
    device_type = DEVICE_TYPE_MAP.get(config['dev'], DeviceType.DEVICE_TYPE_CPU)
    ndev = config['ndev']
    max_tokens = config['max_tokens']
    USE_AWQ = config['awq']
    MAX_BATCH = config['max_batch']
    port = config['port']
    
    print(f"[INFO] 启动推理服务: service_id={service_id}, model_path={model_path}, dev={config['dev']}, port={port}")
    
    @contextlib.asynccontextmanager
    async def lifespan(inference_app: FastAPIApp):
        # Startup
        if USE_AWQ:
            inference_app.state.model = JiugeAWQForCausalLM(
                model_path, device_type, ndev, max_tokens=max_tokens
            )
        else:
            inference_app.state.model = JiugeForCauslLM(
                model_path, device_type, ndev, max_tokens=max_tokens
            )
        inference_app.state.kv_cache_pool = KVCachePool(inference_app.state.model, MAX_BATCH)
        inference_app.state.request_queue = janus.Queue()
        inference_app.state.config = config
        worker_thread = threading.Thread(target=worker_loop, args=(inference_app.state,), daemon=True)
        worker_thread.start()
        
        print(f"[INFO] 推理服务 {service_id} 模型加载完成")

        try:
            yield  # The app runs here
        finally:
            # Shutdown
            inference_app.state.shutdown_event.set()
            inference_app.state.request_queue.sync_q.put(None)
            worker_thread.join(timeout=5)
            inference_app.state.request_queue.shutdown()

            inference_app.state.kv_cache_pool.finalize()
            inference_app.state.model.destroy_model_instance()
            print(f"[INFO] 推理服务 {service_id} 已关闭")
    
    # 创建FastAPI应用，设置lifespan
    inference_app = FastAPIApp(lifespan=lifespan)
    
    # 存储配置和资源
    inference_app.state.config = config
    inference_app.state.service_id = service_id
    inference_app.state.shutdown_event = threading.Event()
    
    def build_task(id_, request_data, app_state):
        """构建推理任务"""
        messages = request_data.get("messages", [])
        input_content = app_state.model.tokenizer.apply_chat_template(
            conversation=messages,
            add_generation_prompt=True,
            tokenize=False,
        )
        tokens = app_state.model.tokenizer.encode(input_content)
        return AsyncInferTask(
            id_,
            tokens,
            request_data.get("max_tokens", app_state.model.max_context_len()),
            request_data.get("temperature", 1.0),
            request_data.get("top_k", 1),
            request_data.get("top_p", 1.0),
            app_state.model.eos_token_id,
        )
    
    async def chat_stream(id_, request_data, req: Request):
        """流式聊天接口"""
        try:
            infer_task = build_task(id_, request_data, req.app.state)
            await req.app.state.kv_cache_pool.acquire(infer_task)

            # 初始空内容
            chunk = json.dumps(
                chunk_json(id_, content="", role="assistant"), ensure_ascii=False
            )
            yield f"data: {chunk}\n\n"

            req.app.state.request_queue.sync_q.put(infer_task)

            while True:
                if await req.is_disconnected():
                    print("Client disconnected. Aborting stream.")
                    break
                if (
                    infer_task.finish_reason is not None
                    and infer_task.output_queue.async_q.empty()
                ):
                    chunk = json.dumps(
                        chunk_json(id_, finish_reason=infer_task.finish_reason),
                        ensure_ascii=False,
                    )
                    yield f"data: {chunk}\n\n"
                    break

                token = await infer_task.output_queue.async_q.get()
                content = req.app.state.model.tokenizer.decode(token)

                chunk = json.dumps(chunk_json(id_, content=content), ensure_ascii=False)
                yield f"data: {chunk}\n\n"

        except Exception as e:
            print(f"[Error] ID : {id_} Exception: {e}")
        finally:
            if infer_task.finish_reason is None:
                infer_task.finish_reason = "cancel"
    
    async def chat(id_, request_data, req: Request):
        """非流式聊天接口"""
        try:
            infer_task = build_task(id_, request_data, req.app.state)
            await req.app.state.kv_cache_pool.acquire(infer_task)
            req.app.state.request_queue.sync_q.put(infer_task)
            output = []
            while True:
                if (
                    infer_task.finish_reason is not None
                    and infer_task.output_queue.async_q.empty()
                ):
                    break

                token = await infer_task.output_queue.async_q.get()
                content = req.app.state.model.tokenizer.decode(token)
                output.append(content)

            output_text = "".join(output).strip()
            response = chunk_json(
                id_,
                content=output_text,
                role="assistant",
                finish_reason=infer_task.finish_reason or "stop",
            )
            return response

        except Exception as e:
            print(f"[Error] ID: {id_} Exception: {e}")
            return FastAPIJSONResponse(content={"error": str(e)}, status_code=500)
        finally:
            if infer_task.finish_reason is None:
                infer_task.finish_reason = "cancel"
    
    @inference_app.post("/chat/completions")
    async def chat_completions(req: Request):
        """聊天完成接口"""
        data = await req.json()
        print('-----------------------------------------')
        print(data)
        print('-----------------------------------------')

        if not data.get("messages"):
            if not data.get("prompt"):
                return FastAPIJSONResponse(content={"error": "No message provided"}, status_code=400)
            else:
                data['messages'] = [{"role": "user", "content": data.get("prompt")}]

        stream = data.get("stream", False)
        id_ = f"cmpl-{uuid.uuid4().hex}"
        if stream:
            return FastAPIStreamingResponse(
                chat_stream(id_, data, req), media_type="text/event-stream"
            )
        else:
            response = await chat(id_, data, req)
            return FastAPIJSONResponse(content=response)
    
    return inference_app, port


def start_inference_service(service_id, config):
    """在独立线程中启动推理服务"""
    try:
        # 动态选择端口：命令指定则优先使用；否则从 DEFAULT_INFERENCE_PORT 起扫描
        if config.get('port'):
            port = int(config['port'])
        else:
            port = find_free_port(DEFAULT_INFERENCE_PORT)
            config['port'] = port

        inference_app, _ = build_inference_app(service_id, config)
        
        # 启动uvicorn服务器
        config_uvicorn = uvicorn.Config(
            app=inference_app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            access_log=False
        )
        server = uvicorn.Server(config_uvicorn)
        
        # 在独立线程中运行服务器
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        
        # 等待服务启动
        time.sleep(3)
        
        # 验证服务是否启动成功
        try:
            # 尝试访问健康检查端点（如果存在）或docs端点
            response = requests.get(f'http://localhost:{port}/docs', timeout=2)
        except:
            pass  # 如果无法连接，可能是服务还在启动中
        
        return inference_app, server, server_thread, port
    except Exception as e:
        print(f"[ERROR] 启动推理服务失败: {e}")
        import traceback
        traceback.print_exc()
        raise


def get_service_status(service_id):
    """获取服务状态"""
    if service_id not in services:
        return {'status': 'stopped', 'message': '服务不存在'}
    
    service_info = services[service_id]
    
    # 如果是推理服务模式
    if service_info.get('inference_app') is not None:
        # 检查推理服务是否还在运行
        try:
            port = service_info.get('port', DEFAULT_INFERENCE_PORT)
            response = requests.get(f'http://localhost:{port}/docs', timeout=1)
            status = 'running'
        except:
            status = 'stopped'
        
        return {
            'status': status,
            'type': 'inference',
            'port': port,
            'config': service_info.get('config', {}),
            'start_time': service_info.get('start_time', '').isoformat() if service_info.get('start_time') else None
        }
    
    # 传统子进程模式
    process = service_info.get('process')
    
    if process is None:
        return {'status': 'stopped', 'message': '服务未运行'}
    
    # 检查进程是否还在运行
    if process.poll() is None:
        # 进程正在运行
        return {
            'status': 'running',
            'type': 'subprocess',
            'pid': process.pid,
            'command': service_info.get('command', ''),
            'start_time': service_info.get('start_time', '').isoformat() if service_info.get('start_time') else None
        }
    else:
        # 进程已结束
        return_code = process.returncode
        services[service_id]['process'] = None
        services[service_id]['status'] = 'stopped'
        return {
            'status': 'stopped',
            'return_code': return_code,
            'message': f'进程已结束，退出码: {return_code}'
        }


@app.route('/health', methods=['GET'])
def health():
    """健康检查接口"""
    return jsonify({'status': 'ok', 'message': 'Service agent is running'})


@app.route('/service/<service_id>/deploy', methods=['POST'])
def deploy_service(service_id):
    """部署服务"""
    try:
        data = request.json or {}
        command = data.get('command', '')
        
        print(f"[INFO] 部署服务: service_id={service_id}, command={command}")
        
        if not command:
            return jsonify({'error': '部署命令不能为空'}), 400
        
        # 如果服务已经在运行，先停止
        if service_id in services:
            print(f"[INFO] 服务 {service_id} 已存在，先停止")
            stop_service_internal(service_id)
        
        # 解析命令，判断是否为推理服务
        config = parse_config_from_command(command)
        print(f"[INFO] 解析配置: {config}")
        
        # 如果配置中有model_path且推理模块可用，则使用推理服务模式
        if config.get('model_path') and INFERENCE_AVAILABLE and FASTAPI_AVAILABLE:
            try:
                inference_app, uvicorn_server, inference_thread, port = start_inference_service(service_id, config)
                
                services[service_id] = {
                    'process': None,
                    'inference_app': inference_app,
                    'uvicorn_server': uvicorn_server,
                    'inference_thread': inference_thread,
                    'command': command,
                    'config': config,
                    'status': 'running',
                    'start_time': datetime.now(),
                    'port': port
                }
                
                print(f"[INFO] 服务 {service_id} 部署成功（推理服务模式），已添加到 services 字典")
                return jsonify({
                    'status': 'deployed',
                    'type': 'inference',
                    'port': port,
                    'message': '推理服务部署成功'
                })
            except Exception as e:
                print(f"[ERROR] 推理服务部署失败: {e}")
                # 回退到子进程模式
                if not config.get('model_path'):
                    raise
                # 继续执行子进程模式
        
        # 子进程模式（传统方式或回退）
        env = get_env_with_path()
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        
        services[service_id] = {
            'process': process,
            'inference_app': None,
            'uvicorn_server': None,
            'inference_thread': None,
            'command': command,
            'config': None,
            'status': 'running',
            'start_time': datetime.now(),
            'port': None
        }
        
        print(f"[INFO] 服务 {service_id} 部署成功（子进程模式），已添加到 services 字典")
        return jsonify({
            'status': 'deployed',
            'type': 'subprocess',
            'pid': process.pid,
            'message': '服务部署成功'
        })
    except Exception as e:
        import traceback
        print(f"[ERROR] 部署服务 {service_id} 失败: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def stop_service_internal(service_id):
    """内部停止服务方法"""
    if service_id not in services:
        return
    
    service_info = services[service_id]
    
    # 停止推理服务
    if service_info.get('inference_app'):
        try:
            # 请求 uvicorn 退出（否则端口会一直占用，导致 address already in use）
            if service_info.get('uvicorn_server') is not None:
                service_info['uvicorn_server'].should_exit = True

            # 触发shutdown事件
            if hasattr(service_info['inference_app'].state, 'shutdown_event'):
                service_info['inference_app'].state.shutdown_event.set()
            # 等待线程结束
            if service_info.get('inference_thread'):
                service_info['inference_thread'].join(timeout=5)
        except Exception as e:
            print(f"[ERROR] 停止推理服务失败: {e}")
    
    # 停止子进程
    process = service_info.get('process')
    if process and process.poll() is None:
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            else:
                process.terminate()
            
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name != 'nt':
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        except Exception as e:
            print(f"[ERROR] 停止进程失败: {e}")


@app.route('/service/<service_id>/restart', methods=['POST'])
def restart_service(service_id):
    """重启服务"""
    try:
        data = request.json or {}
        command = data.get('command', '')
        
        # 如果服务不存在但有命令，尝试部署（等同于重启）
        if service_id not in services:
            if command:
                # 没有服务但有命令，尝试部署
                return deploy_service(service_id)
            else:
                return jsonify({'error': '服务不存在且未提供部署命令'}), 404
        
        # 如果服务存在，先获取命令
        if not command:
            # 如果没有提供命令，使用之前的命令
            command = services[service_id].get('command', '')
            if not command:
                return jsonify({'error': '重启命令不能为空'}), 400
        
        # 先停止服务
        stop_service_internal(service_id)
        
        # 等待一小段时间确保服务完全停止
        time.sleep(1)
        
        # 重新部署（使用相同的逻辑）
        data['command'] = command
        return deploy_service(service_id)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/service/<service_id>/stop', methods=['POST'])
def stop_service(service_id):
    """停止服务"""
    try:
        if service_id not in services:
            return jsonify({'error': '服务不存在'}), 404
        
        stop_service_internal(service_id)
        
        services[service_id]['process'] = None
        services[service_id]['inference_app'] = None
        services[service_id]['uvicorn_server'] = None
        services[service_id]['inference_thread'] = None
        services[service_id]['status'] = 'stopped'
        
        return jsonify({'status': 'stopped', 'message': '服务已停止'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/service/<service_id>/status', methods=['GET'])
def get_service_status_endpoint(service_id):
    """获取服务状态"""
    try:
        status = get_service_status(service_id)
        return jsonify(status)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/service/<service_id>/chat/completions', methods=['POST'])
def chat_completions_proxy(service_id):
    """聊天完成接口（代理到推理服务）"""
    try:
        if service_id not in services:
            return jsonify({'error': '服务不存在'}), 404
        
        service_info = services[service_id]
        inference_app = service_info.get('inference_app')
        
        if not inference_app:
            return jsonify({'error': '该服务不是推理服务'}), 400
        
        port = service_info.get('port', DEFAULT_INFERENCE_PORT)
        target_url = f'http://localhost:{port}/chat/completions'
        
        # 获取请求数据
        request_data = request.json or {}
        
        is_stream = bool(request_data.get('stream', False))

        # 如果是流式响应
        if is_stream:
            try:
                headers = {
                    'Content-Type': 'application/json',
                    'Accept': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    # 禁用压缩，避免 requests/urllib3 因 gzip 缓冲导致“最后一次性输出”
                    'Accept-Encoding': 'identity',
                }
                response = requests.post(
                    target_url,
                    json=request_data,
                    headers=headers,
                    timeout=120,
                    stream=True
                )
                
                def generate():
                    try:
                        # 直接原样转发 bytes，避免 iter_lines/换行处理造成缓冲
                        for chunk in response.iter_content(chunk_size=None):
                            if chunk:
                                yield chunk
                    finally:
                        try:
                            response.close()
                        except Exception:
                            pass
                
                return Response(
                    stream_with_context(generate()),
                    mimetype='text/event-stream',
                    direct_passthrough=True,
                    headers={
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive',
                        'X-Accel-Buffering': 'no'
                    }
                )
            except requests.exceptions.RequestException as e:
                return jsonify({'error': f'连接失败: {str(e)}'}), 500
        else:
            # 非流式响应
            try:
                response = requests.post(
                    target_url,
                    json=request_data,
                    headers={'Content-Type': 'application/json'},
                    timeout=120
                )
                if response.status_code == 200:
                    return jsonify(response.json())
                else:
                    return jsonify({'error': f'API请求失败: {response.text}'}), response.status_code
            except requests.exceptions.RequestException as e:
                return jsonify({'error': f'连接失败: {str(e)}'}), 500
                
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/services', methods=['GET'])
def list_services():
    """列出所有服务"""
    result = {}
    for service_id, info in services.items():
        status = get_service_status(service_id)
        result[service_id] = status
    return jsonify(result)


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    print(f'Service agent starting on port {port}...')
    app.run(host='0.0.0.0', port=port, debug=False)
