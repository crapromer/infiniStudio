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
import uuid
import requests

# 推理服务相关导入（新版：参考 inference_server.py，进程内直接调用 InferEngine）
try:
    import infinicore
    from infinilm.core.scheduler import Scheduler
    from infinilm.core.request import RequestStatus, InferenceRequest
    from infinilm.distributed import DistConfig
    from infinilm.infer_engine import InferEngine
    from transformers import AutoTokenizer
    from tokenizers import decoders as _dec
    from infinilm.cache.cache import PagedKVCacheConfig
    from infinilm.modeling_utils import load_model_state_dict_by_file
    INFERENCE_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] 推理依赖未找到: {e}")
    print("[WARNING] 将回退到子进程模式启动服务")
    INFERENCE_AVAILABLE = False

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

# 存储服务进程信息
# {service_id: {
#     'process': subprocess.Popen or None,
#     'runtime': dict or None,  # 进程内推理运行时资源
#     'runtime_lock': threading.Lock,
#     'command': str,
#     'config': dict,  # 推理服务配置
#     'status': str,
#     'start_time': datetime,
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
            # 支持 --key=value
            if '=' in part:
                k, v = part[2:].split('=', 1)
                key = k.replace('-', '_')
                args_dict[key] = v
                i += 1
            else:
                key = part[2:].replace('-', '_')
                if i + 1 < len(parts) and (not parts[i + 1].startswith('--')):
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

    # 兼容旧字段/新字段
    model_path = config.get('model_path') or config.get('model-path') or ''

    # device flags（inference_server.py 的 CLI 风格：--cpu/--nvidia/...）
    device_flag = 'cpu'
    if config.get('nvidia'):
        device_flag = 'cuda'
    elif config.get('metax'):
        device_flag = 'cuda'
    elif config.get('iluvatar'):
        device_flag = 'cuda'
    elif config.get('moore'):
        device_flag = 'moore'
    elif config.get('cambricon'):
        device_flag = 'mlu'
    elif config.get('cpu'):
        device_flag = 'cpu'

    # tp（张量并行）/ndev 兼容
    tp = config.get('tp') or config.get('ndev') or 1
    try:
        tp = int(tp)
    except Exception:
        tp = 1

    # max_tokens / max_batch_size
    max_tokens = config.get('max_tokens') or config.get('max-tokens') or config.get('max_tokens'.replace('-', '_'))
    try:
        max_tokens = int(max_tokens) if max_tokens is not None else 512
    except Exception:
        max_tokens = 512

    max_batch_size = (
        config.get('max_batch_size')
        or config.get('max-batch-size')
        or config.get('max_batch')
        or config.get('max-batch')
        or 8
    )
    try:
        max_batch_size = int(max_batch_size)
    except Exception:
        max_batch_size = 8

    # backend / dtype / sampling
    backend = config.get('backend') or 'cpp'
    dtype = config.get('dtype') or 'float16'
    temperature = config.get('temperature') or 1.0
    top_p = config.get('top_p') or config.get('top-p') or 0.8
    top_k = config.get('top_k') or config.get('top-k') or 1
    num_blocks = config.get('num_blocks') or config.get('num-blocks') or 8 * 1024
    block_size = config.get('block_size') or config.get('block-size') or 16

    try:
        temperature = float(temperature)
    except Exception:
        temperature = 1.0
    try:
        top_p = float(top_p)
    except Exception:
        top_p = 0.8
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 1
    try:
        num_blocks = int(num_blocks)
    except Exception:
        num_blocks = 8 * 1024
    try:
        block_size = int(block_size)
    except Exception:
        block_size = 16

    # port 可选：如果命令未指定，则后续动态分配，避免端口冲突
    port = None
    if config.get('port'):
        try:
            port = int(config.get('port'))
        except Exception:
            port = None

    return {
        'model_path': model_path,
        'device': device_flag,           # 'cpu' | 'cuda' | 'moore' | 'mlu'
        'dtype': dtype,                  # 'float16' | 'float32' | 'bfloat16'
        'tp': tp,                        # tensor parallel
        'max_tokens': max_tokens,
        'max_batch_size': max_batch_size,
        'backend': backend,              # 'cpp' | 'python'
        'num_blocks': num_blocks,
        'block_size': block_size,
        'temperature': temperature,
        'top_p': top_p,
        'top_k': top_k,
        'port': port,
    }


def _build_runtime(service_id: str, config: dict) -> dict:
    """
    进程内构建推理运行时（参考 inference_server.py 的 lifespan 初始化逻辑）
    注意：不启动独立 HTTP 推理服务，不使用 uvicorn 线程。
    """
    model_path = config.get("model_path") or ""
    if not model_path:
        raise RuntimeError("model_path 不能为空")

    device_str = config.get("device") or "cpu"
    infini_device = infinicore.device(device_str, 0)

    dtype_str = (config.get("dtype") or "float16").lower()
    if dtype_str == "float32":
        infini_dtype = infinicore.float32
    elif dtype_str == "bfloat16":
        infini_dtype = infinicore.bfloat16
    else:
        infini_dtype = infinicore.float16

    tp = int(config.get("tp") or 1)
    max_tokens = int(config.get("max_tokens") or 512)
    max_batch_size = int(config.get("max_batch_size") or 8)
    backend = config.get("backend") or "cpp"
    num_blocks = int(config.get("num_blocks") or (8 * 1024))
    block_size = int(config.get("block_size") or 16)

    temperature = float(config.get("temperature") or 1.0)
    top_p = float(config.get("top_p") or 0.8)
    top_k = int(config.get("top_k") or 1)

    print(
        f"[INFO] 初始化推理运行时: service_id={service_id}, model_path={model_path}, device={device_str}, "
        f"dtype={dtype_str}, tp={tp}, backend={backend}"
    )

    engine = InferEngine(
        model_path=model_path,
        device=infini_device,
        distributed_config=DistConfig(tp),
    )
    load_model_state_dict_by_file(engine, model_path, dtype=engine.config.dtype)

    tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    # llama decoder 修复（与 inference_server.py 保持一致）
    try:
        if "llama" in engine.config.model_type:
            backend_tok = getattr(tokenizer, "backend_tokenizer", None)
            target = getattr(backend_tok, "_tokenizer", backend_tok)
            norm = getattr(target, "normalizer", None)
            dec = getattr(target, "decoder", None)
            sn = repr(norm)[:800] if norm is not None else ""
            sd = repr(dec)[:800] if dec is not None else ""
            has_prepend = "Prepend" in sn
            has_strip = "Strip" in sd
            if has_prepend and has_strip:
                target.decoder = _dec.Sequence(
                    [
                        _dec.Replace("▁", " "),
                        _dec.ByteFallback(),
                        _dec.Fuse(),
                    ]
                )
    except Exception as e:
        print(f"[WARNING] llama decoder patch 失败: {e}")

    cache_config = PagedKVCacheConfig(num_blocks=num_blocks, block_size=block_size)
    engine.reset_cache(cache_config)

    scheduler = Scheduler(
        max_batch_size=max_batch_size,
        num_blocks=num_blocks,
        block_size=block_size,
    )

    return {
        "engine": engine,
        "tokenizer": tokenizer,
        "scheduler": scheduler,
        "config": {
            **config,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "max_tokens": max_tokens,
        },
    }


def _check_request_finished(req, token_id) -> bool:
    """参考 inference_server.py:_check_request_finished"""
    # Check max length
    if req.get_num_generated_tokens() >= req.max_new_tokens:
        req.finish_reason = "length"
        return True
    # Check EOS token
    if req.eos_token_id and token_id in req.eos_token_id:
        req.finish_reason = "eos_token"
        return True
    # Check end strings
    for end_str in getattr(req, "end_strings", []) or []:
        if req.generated_text.endswith(end_str):
            req.finish_reason = "end_string"
            return True
    return False


def _to_infinicore_inputs(model_input_dict: dict) -> dict:
    """参考 inference_server.py:_step_loop 里对 model_input_dict 的转换"""
    model_input = {}
    for key, value in model_input_dict.items():
        if key == "input_ids":
            model_input[key] = infinicore.from_list([value], dtype=infinicore.int64)
        elif key in [
            "position_ids",
            "past_kv_lengths",
            "total_kv_lengths",
            "input_offsets",
            "slot_mapping",
        ]:
            model_input[key] = infinicore.from_list(value, dtype=infinicore.int64)
        elif key == "block_tables":
            model_input[key] = infinicore.from_list(value, dtype=infinicore.int64)
        else:
            model_input[key] = value
    return model_input


def _infer_stream(runtime: dict, request_data: dict):
    """单请求流式推理：不依赖后台线程，边 step 边 SSE 输出"""
    cfg = runtime["config"]
    engine = runtime["engine"]
    tokenizer = runtime["tokenizer"]
    scheduler = runtime["scheduler"]

    messages = request_data.get("messages", [])
    input_content = tokenizer.apply_chat_template(
        conversation=messages,
        add_generation_prompt=True,
        tokenize=False,
    )
    input_tokens = tokenizer.encode(input_content)

    req_id = f"cmpl-{uuid.uuid4().hex}"
    req = InferenceRequest(
        req_id,
        request_data,
        None,  # FastAPI Request 不可用，传 None（仅用于断连检查/日志）
        input_tokens,
        request_data.get("max_tokens", cfg.get("max_tokens", 512)),
        request_data.get("temperature", cfg.get("temperature", 1.0)),
        request_data.get("top_k", cfg.get("top_k", 1)),
        request_data.get("top_p", cfg.get("top_p", 0.8)),
        engine.config.eos_token_id,
    )

    scheduler.add_request(req)

    # 初始空 chunk（与 OpenAI 兼容）
    first = json.dumps(chunk_json(req_id, content="", role="assistant"), ensure_ascii=False)
    yield f"data: {first}\n\n".encode("utf-8")

    start_time = time.time()
    # 同步 step loop（不启独立线程）
    while True:
        # 超时保护
        if time.time() - start_time > 1000.0:
            req.status = RequestStatus.Timeout
            req.finish_reason = "timeout"
            err_chunk = json.dumps(
                chunk_json(req_id, content="[Request timeout]", finish_reason="timeout"),
                ensure_ascii=False,
            )
            yield f"data: {err_chunk}\n\n".encode("utf-8")
            break

        if req.finish_reason is not None:
            end_chunk = json.dumps(chunk_json(req_id, finish_reason=req.finish_reason), ensure_ascii=False)
            yield f"data: {end_chunk}\n\n".encode("utf-8")
            break

        scheduler_output = scheduler.schedule()
        if scheduler_output is None:
            time.sleep(0.01)
            continue
        if not getattr(scheduler_output, "scheduled_requests", []):
            time.sleep(0.01)
            continue

        # Build model inputs
        model_input_dict = scheduler_output.build_model_inputs(
            request_data.get("temperature", cfg.get("temperature", 1.0)),
            request_data.get("top_p", cfg.get("top_p", 0.8)),
            request_data.get("top_k", cfg.get("top_k", 1)),
        )
        model_input = _to_infinicore_inputs(model_input_dict)

        sampled_tokens = engine.forward(**model_input)
        sampled_tokens_list = sampled_tokens.to_numpy().tolist()

        # prefill 时重置 req blocks（与 inference_server 保持一致）
        if getattr(scheduler_output, "is_prefill", False):
            try:
                scheduler.cache_manager.reset_req_blocks()
            except Exception:
                pass

        scheduled = scheduler_output.scheduled_requests
        for _r, token_id in zip(scheduled, sampled_tokens_list):
            _r.generated_token_ids.append(token_id)
            if getattr(_r, "is_prefill", False):
                _r.is_prefill = False

            token_text = tokenizer.decode(token_id)
            _r.generated_text += token_text

            if _check_request_finished(_r, token_id):
                _r.status = RequestStatus.Finished
                _r.finished_time = time.time()

            # SSE 输出本 token
            out_chunk = json.dumps(chunk_json(req_id, content=token_text), ensure_ascii=False)
            yield f"data: {out_chunk}\n\n".encode("utf-8")

        # 通知 scheduler 本 step 完成
        try:
            scheduler.complete_requests(scheduled)
        except Exception:
            pass

    yield b"data: [DONE]\n\n"


def _infer_once(runtime: dict, request_data: dict) -> dict:
    """非流式推理：同步 step 直到结束，最后返回完整响应"""
    output_text_parts = []
    for b in _infer_stream(runtime, {**request_data, "stream": True}):
        try:
            s = b.decode("utf-8", errors="ignore")
        except Exception:
            continue
        # 只拼 content（忽略 [DONE] / finish_reason chunk）
        if not s.startswith("data: "):
            continue
        payload = s[len("data: ") :].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
            choices = (obj or {}).get("choices") or []
            if choices and isinstance(choices, list):
                delta = choices[0].get("delta") or {}
                c = delta.get("content") or choices[0].get("text")
                if c:
                    output_text_parts.append(c)
        except Exception:
            continue

    full_text = "".join(output_text_parts).strip()
    rid = f"cmpl-{uuid.uuid4().hex}"
    return chunk_json(rid, content=full_text, role="assistant", finish_reason="stop")


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


def build_inference_app(service_id, config):
    """构建推理服务的FastAPI应用（基于 inference_server.InferenceServer）"""
    if not INFERENCE_AVAILABLE or not FASTAPI_AVAILABLE:
        raise RuntimeError("推理服务模块不可用")

    model_path = config.get('model_path') or ''
    if not model_path:
        raise RuntimeError("model_path 不能为空")

    # device / dtype 映射到 infinicore 对象
    device_str = config.get('device') or 'cpu'  # 'cpu'|'cuda'|'moore'|'mlu'
    infini_device = infinicore.device(device_str, 0)

    dtype_str = (config.get('dtype') or 'float16').lower()
    if dtype_str == 'float32':
        infini_dtype = infinicore.float32
    elif dtype_str == 'bfloat16':
        infini_dtype = infinicore.bfloat16
    else:
        infini_dtype = infinicore.float16

    server_obj = InferenceServer(
        model_path=model_path,
        device=infini_device,
        dtype=infini_dtype,
        ndev=int(config.get('tp') or 1),
        max_tokens=int(config.get('max_tokens') or 512),
        max_batch_size=int(config.get('max_batch_size') or 8),
        backend=config.get('backend') or 'cpp',
        num_blocks=int(config.get('num_blocks') or (8 * 1024)),
        block_size=int(config.get('block_size') or 16),
        temperature=float(config.get('temperature') or 1.0),
        top_p=float(config.get('top_p') or 0.8),
        top_k=int(config.get('top_k') or 1),
    )

    inference_app = server_obj._create_app()
    return inference_app, server_obj, config.get('port')


def start_inference_service(service_id, config):
    """在独立线程中启动推理服务"""
    try:
        # 动态选择端口：命令指定则优先使用；否则从 DEFAULT_INFERENCE_PORT 起扫描
        if config.get('port'):
            port = int(config['port'])
        else:
            port = find_free_port(DEFAULT_INFERENCE_PORT)
            config['port'] = port

        inference_app, server_obj, _ = build_inference_app(service_id, config)
        
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
        
        return inference_app, server_obj, server, server_thread, port
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
    
    # 如果是进程内推理模式
    if service_info.get("runtime") is not None:
        return {
            "status": "running",
            "type": "inference",
            "config": service_info.get("config", {}),
            "start_time": service_info.get("start_time", "").isoformat()
            if service_info.get("start_time")
            else None,
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
        
        # 如果配置中有 model_path 且推理依赖可用，则使用“进程内推理模式”
        if config.get("model_path") and INFERENCE_AVAILABLE:
            try:
                runtime = _build_runtime(service_id, config)
                
                services[service_id] = {
                    'process': None,
                    'runtime': runtime,
                    'runtime_lock': threading.Lock(),
                    'command': command,
                    'config': config,
                    'status': 'running',
                    'start_time': datetime.now(),
                }
                
                print(f"[INFO] 服务 {service_id} 部署成功（进程内推理模式），已添加到 services 字典")
                return jsonify({
                    'status': 'deployed',
                    'type': 'inference',
                    'message': '推理服务部署成功'
                })
            except Exception as e:
                print(f"[ERROR] 推理服务部署失败: {e}")
                # 回退到子进程模式
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
            'runtime': None,
            'runtime_lock': threading.Lock(),
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
    
    # 停止进程内推理运行时（释放引用，交由底层/GC 回收）
    if service_info.get("runtime") is not None:
        try:
            service_info["runtime"] = None
        except Exception as e:
            print(f"[ERROR] 停止推理运行时失败: {e}")
    
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
        services[service_id]['runtime'] = None
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
    """聊天完成接口（进程内推理，不启动独立 HTTP 推理服务）"""
    try:
        if service_id not in services:
            return jsonify({'error': '服务不存在'}), 404
        
        service_info = services[service_id]
        runtime = service_info.get("runtime")
        if runtime is None:
            return jsonify({'error': '该服务不是推理服务'}), 400
        
        # 获取请求数据
        request_data = request.json or {}
        
        is_stream = bool(request_data.get('stream', False))

        # 如果是流式响应
        if is_stream:
            lock = service_info.get("runtime_lock") or threading.Lock()
            def generate_bytes():
                # 串行化同一服务上的推理，避免 scheduler/kv cache 并发冲突
                with lock:
                    for b in _infer_stream(runtime, request_data):
                        yield b

            return Response(
                stream_with_context(generate_bytes()),
                mimetype="text/event-stream",
                direct_passthrough=True,
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            # 非流式响应
            lock = service_info.get("runtime_lock") or threading.Lock()
            with lock:
                resp_obj = _infer_once(runtime, request_data)
            return jsonify(resp_obj)
                
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
