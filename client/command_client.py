#!/usr/bin/env python3
"""
命令执行客户端代理
运行在服务器上，提供HTTP接口用于执行命令并实时返回结果
"""

from flask import Flask, request, jsonify, Response, stream_with_context
import subprocess
import threading
import os
import time
import uuid
import json
import argparse
from datetime import datetime

app = Flask(__name__)

# 存储执行任务信息
# {task_id: {
#     'process': subprocess.Popen,
#     'script_content': str,
#     'start_time': datetime,
#     'status': str,  # running, completed, error
#     'output': str,
#     'error': str
# }}
executing_tasks = {}
tasks_lock = threading.Lock()

# 默认端口
DEFAULT_PORT = 9090  # command_client 监听端口（与service_agent区分）


def execute_command_streaming(command, task_id):
    """执行命令并实时返回输出（生成器函数）"""
    script_filename = None
    process = None
    
    try:
        # 创建临时Python脚本文件
        script_filename = f'/tmp/command_{task_id}.py'
        
        # 如果是Python脚本内容，写入文件
        if isinstance(command, dict) and 'script' in command:
            script_content = command['script']
            with open(script_filename, 'w', encoding='utf-8') as f:
                f.write(script_content)
            os.chmod(script_filename, 0o755)
            exec_command = ['python3', script_filename]
        else:
            # 直接执行命令
            exec_command = command if isinstance(command, list) else command.split()
        
        # 启动进程
        process = subprocess.Popen(
            exec_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 合并stderr到stdout
            text=True,
            bufsize=1,  # 行缓冲
            universal_newlines=True
        )
        
        # 更新任务状态
        with tasks_lock:
            executing_tasks[task_id] = {
                'process': process,
                'script_content': command.get('script', '') if isinstance(command, dict) else '',
                'start_time': datetime.now(),
                'status': 'running',
                'output': '',
                'error': ''
            }
        
        output_buffer = ''
        
        # 实时读取输出
        for line in iter(process.stdout.readline, ''):
            if not line:
                break
            output_buffer += line
            # 实时发送输出（SSE格式）
            yield f"data: {json.dumps({'type': 'output', 'data': line, 'is_error': False})}\n\n"
        
        # 等待进程完成
        return_code = process.wait()
        
        # 更新任务状态
        with tasks_lock:
            if task_id in executing_tasks:
                executing_tasks[task_id]['status'] = 'completed' if return_code == 0 else 'error'
                executing_tasks[task_id]['output'] = output_buffer
                executing_tasks[task_id]['return_code'] = return_code
        
        # 发送完成状态
        yield f"data: {json.dumps({'type': 'status', 'status': 'completed' if return_code == 0 else 'error', 'return_code': return_code})}\n\n"
        
    except Exception as e:
        error_msg = str(e)
        # 更新任务状态为错误
        with tasks_lock:
            if task_id in executing_tasks:
                executing_tasks[task_id]['status'] = 'error'
                executing_tasks[task_id]['error'] = error_msg
        
        yield f"data: {json.dumps({'type': 'error', 'data': error_msg})}\n\n"
        yield f"data: {json.dumps({'type': 'status', 'status': 'error'})}\n\n"
    
    finally:
        # 清理临时文件
        if script_filename:
            try:
                if os.path.exists(script_filename):
                    os.remove(script_filename)
            except:
                pass
        
        # 确保进程已关闭
        if process and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except:
                try:
                    process.kill()
                except:
                    pass


@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': 'ok', 'message': 'Command client is running'})


@app.route('/command/execute', methods=['POST'])
def execute_command():
    """执行命令（流式返回）"""
    try:
        data = request.json
        command = data.get('command', '')
        script = data.get('script', '')
        
        if not command and not script:
            return jsonify({'error': 'Command or script is required'}), 400
        
        # 生成任务ID
        task_id = str(uuid.uuid4())
        
        # 确定执行内容
        if script:
            exec_data = {'script': script}
        else:
            exec_data = command
        
        # 创建流式响应
        return Response(
            stream_with_context(execute_command_streaming(exec_data, task_id)),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            }
        )
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/command/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """获取任务状态"""
    with tasks_lock:
        if task_id in executing_tasks:
            task = executing_tasks[task_id]
            return jsonify({
                'task_id': task_id,
                'status': task['status'],
                'start_time': task['start_time'].isoformat(),
                'output': task.get('output', ''),
                'error': task.get('error', ''),
                'return_code': task.get('return_code', None)
            })
        else:
            return jsonify({'error': 'Task not found'}), 404


@app.route('/command/<task_id>/stop', methods=['POST'])
def stop_task(task_id):
    """停止任务"""
    with tasks_lock:
        if task_id in executing_tasks:
            task = executing_tasks[task_id]
            process = task.get('process')
            if process and process.poll() is None:
                process.terminate()
                # 等待进程结束
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                
                task['status'] = 'stopped'
                return jsonify({'message': 'Task stopped'})
            else:
                return jsonify({'error': 'Task is not running'}), 400
        else:
            return jsonify({'error': 'Task not found'}), 404


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Command Client Agent')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'Port to listen on (default: {DEFAULT_PORT})')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Host to bind to (default: 0.0.0.0)')
    args = parser.parse_args()
    
    print(f"[INFO] Command client starting on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True, debug=False)
