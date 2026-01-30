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
import pty
import select
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
    """执行命令并实时返回输出（生成器函数）
    使用pty（伪终端）模拟invoke_shell行为，确保环境变量被正确加载
    """
    script_filename = None
    master_fd = None
    pid = None
    
    try:
        # 创建临时Python脚本文件
        script_filename = f'/tmp/command_{task_id}.py'
        
        # 如果是Python脚本内容，写入文件
        if isinstance(command, dict) and 'script' in command:
            script_content = command['script']
            with open(script_filename, 'w', encoding='utf-8') as f:
                f.write(script_content)
            os.chmod(script_filename, 0o755)
            
            # 构建执行命令
            exec_command = f'python3 {script_filename}'
            
            # 如果有命令行参数，添加到命令后面
            if 'args' in command and command['args']:
                exec_command += f" {command['args']}"
            
            # 使用bash -l -c执行，确保加载所有环境变量（.bashrc, .bash_profile等）
            # 与命令执行方式保持一致
            import shlex
            full_command = f"bash -l -c {shlex.quote(exec_command)}"
            use_heredoc = False
        else:
            # 直接执行命令
            if isinstance(command, list):
                exec_command = ' '.join(command)
            else:
                exec_command = command
            
            # 对于cd等内置命令，使用bash -l -c来执行
            # 这样可以正确处理shell内置命令
            import shlex
            full_command = f"bash -l -c {shlex.quote(exec_command)}"
            use_heredoc = False
        
        # 创建伪终端（pty），模拟invoke_shell行为
        # 这样可以获得完整的shell环境，包括所有环境变量
        master_fd, slave_fd = pty.openpty()
        
        # 在子进程中执行命令
        pid = os.fork()
        if pid == 0:
            # 子进程
            os.close(master_fd)
            os.dup2(slave_fd, 0)  # stdin
            os.dup2(slave_fd, 1)  # stdout
            os.dup2(slave_fd, 2)  # stderr
            os.close(slave_fd)
            
            # 执行命令
            # 统一使用bash -l -c方式执行，确保环境变量被正确加载
            if use_heredoc:
                # heredoc格式（保留兼容性，但通常不会执行到这里）
                os.execv('/bin/bash', ['/bin/bash', '-l', '-c', full_command])
            else:
                # bash -l -c格式，直接使用exec_command
                # exec_command已经包含了完整的命令（python3 script.py args 或 shell命令）
                os.execv('/bin/bash', ['/bin/bash', '-l', '-c', exec_command])
        else:
            # 父进程
            os.close(slave_fd)
            
            # 更新任务状态
            with tasks_lock:
                executing_tasks[task_id] = {
                    'process': None,  # 使用pid管理
                    'pid': pid,
                    'master_fd': master_fd,
                    'script_content': command.get('script', '') if isinstance(command, dict) else '',
                    'start_time': datetime.now(),
                    'status': 'running',
                    'output': '',
                    'error': ''
                }
            
            output_buffer = ''
            return_code = -1
            
            # 实时读取输出
            while True:
                # 使用select等待数据可读
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if ready:
                    try:
                        data = os.read(master_fd, 1024)
                        if not data:
                            # 没有数据了，检查进程状态
                            try:
                                wait_result = os.waitpid(pid, os.WNOHANG)
                                if wait_result[0] == pid:
                                    return_code = os.WEXITSTATUS(wait_result[1]) if os.WIFEXITED(wait_result[1]) else -1
                                    break
                            except (OSError, ChildProcessError):
                                break
                            break
                        
                        # 解码数据
                        try:
                            text = data.decode('utf-8', errors='replace')
                        except:
                            text = data.decode('latin-1', errors='replace')
                        
                        output_buffer += text
                        # 实时发送输出（SSE格式）
                        yield f"data: {json.dumps({'type': 'output', 'data': text, 'is_error': False})}\n\n"
                    except OSError:
                        break
                else:
                    # 检查进程是否还在运行
                    try:
                        # 使用waitpid的非阻塞模式检查进程状态
                        wait_result = os.waitpid(pid, os.WNOHANG)
                        if wait_result[0] == pid:
                            # 进程已结束，读取剩余数据
                            while True:
                                ready, _, _ = select.select([master_fd], [], [], 0.1)
                                if ready:
                                    try:
                                        data = os.read(master_fd, 1024)
                                        if not data:
                                            break
                                        text = data.decode('utf-8', errors='replace')
                                        output_buffer += text
                                        yield f"data: {json.dumps({'type': 'output', 'data': text, 'is_error': False})}\n\n"
                                    except OSError:
                                        break
                                else:
                                    break
                            
                            # 获取退出码
                            return_code = os.WEXITSTATUS(wait_result[1]) if os.WIFEXITED(wait_result[1]) else -1
                            break
                    except (OSError, ChildProcessError):
                        # 进程可能已经结束，尝试等待
                        try:
                            wait_result = os.waitpid(pid, 0)
                            return_code = os.WEXITSTATUS(wait_result[1]) if os.WIFEXITED(wait_result[1]) else -1
                        except (OSError, ChildProcessError):
                            return_code = -1
                        break
            
            # 确保进程已结束（如果还没结束）
            if return_code == -1:
                try:
                    wait_result = os.waitpid(pid, 0)
                    return_code = os.WEXITSTATUS(wait_result[1]) if os.WIFEXITED(wait_result[1]) else -1
                except (OSError, ChildProcessError):
                    return_code = -1
            
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
        if pid:
            try:
                # 尝试终止进程
                os.kill(pid, 15)  # SIGTERM
                time.sleep(0.5)
                # 如果还在运行，强制杀死
                try:
                    os.waitpid(pid, os.WNOHANG)
                except:
                    try:
                        os.kill(pid, 9)  # SIGKILL
                        os.waitpid(pid, 0)
                    except:
                        pass
            except (OSError, ProcessLookupError):
                pass
        
        # 关闭伪终端
        if master_fd:
            try:
                os.close(master_fd)
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
        args = data.get('args', '')  # 获取命令行参数
        
        if not command and not script:
            return jsonify({'error': 'Command or script is required'}), 400
        
        # 生成任务ID
        task_id = str(uuid.uuid4())
        
        # 确定执行内容
        if script:
            exec_data = {'script': script}
            if args:
                exec_data['args'] = args  # 添加命令行参数
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
