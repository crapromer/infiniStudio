from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import sqlite3
import os
import threading
import time
import paramiko
from datetime import datetime
import json
from werkzeug.utils import secure_filename
import uuid
import subprocess
import platform
import requests

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

DATABASE = os.path.join(os.path.dirname(__file__), '..', 'datasets', 'infini.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def init_db():
    """初始化数据库"""
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # 品牌表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            logo TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 为现有表添加sort_order列（如果不存在）
    try:
        cursor.execute('ALTER TABLE brands ADD COLUMN sort_order INTEGER DEFAULT 0')
    except sqlite3.OperationalError:
        # 列已存在，忽略错误
        pass
    
    # 加速卡表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS accelerator_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            model TEXT NOT NULL,
            memory TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands(id)
        )
    ''')
    
    # 模型表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            logo TEXT,
            parameters TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 为现有表添加parameters列（如果不存在）
    try:
        cursor.execute('ALTER TABLE models ADD COLUMN parameters TEXT')
    except sqlite3.OperationalError:
        # 列已存在，忽略错误
        pass
    
    # 服务器表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            brand_id INTEGER,
            model_id INTEGER,
            host_ip TEXT NOT NULL,
            port INTEGER DEFAULT 22,
            username TEXT NOT NULL,
            password TEXT,
            agent_port INTEGER DEFAULT 8888,
            status TEXT DEFAULT 'offline',
            last_check TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (brand_id) REFERENCES brands(id),
            FOREIGN KEY (model_id) REFERENCES accelerator_cards(id)
        )
    ''')
    
    # 为现有表添加agent_port列（如果不存在）
    try:
        cursor.execute('ALTER TABLE servers ADD COLUMN agent_port INTEGER DEFAULT 8888')
    except sqlite3.OperationalError:
        pass
    
    # 服务表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            model_id INTEGER NOT NULL,
            server_ids TEXT NOT NULL,
            deploy_command TEXT,
            deploy_status TEXT DEFAULT 'pending',
            deploy_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (model_id) REFERENCES models(id)
        )
    ''')
    
    # 为现有表添加deploy_command和deploy_status列（如果不存在）
    try:
        cursor.execute('ALTER TABLE services ADD COLUMN deploy_command TEXT')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE services ADD COLUMN deploy_status TEXT DEFAULT "pending"')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE services ADD COLUMN deploy_result TEXT')
    except sqlite3.OperationalError:
        pass
    
    # 聊天记录表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (service_id) REFERENCES services(id)
        )
    ''')
    
    # 计划任务表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            command TEXT NOT NULL,
            server_id INTEGER NOT NULL,
            schedule_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            last_run TIMESTAMP,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (server_id) REFERENCES servers(id)
        )
    ''')
    
    # 为现有表添加result列（如果不存在）
    try:
        cursor.execute('ALTER TABLE tasks ADD COLUMN result TEXT')
    except sqlite3.OperationalError:
        # 列已存在，忽略错误
        pass
    
    conn.commit()
    conn.close()

init_db()

# SSH连接池
ssh_connections = {}  # 临时连接：{session_id: {...}}
service_ssh_connections = {}  # 服务持久化连接：{service_id: {ssh, chan, sessions: [session_id, ...]}}

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== 文件上传 ====================

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """上传文件接口"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        # 生成唯一文件名
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4()}.{ext}"
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        
        # 返回文件URL
        file_url = f"/api/uploads/{filename}"
        return jsonify({'url': file_url, 'filename': filename}), 200
    else:
        return jsonify({'error': 'Invalid file type'}), 400

@app.route('/api/uploads/<filename>')
def uploaded_file(filename):
    """返回上传的文件"""
    return send_from_directory(UPLOAD_FOLDER, filename)

# ==================== 品牌管理 ====================

@app.route('/api/brands', methods=['GET'])
def get_brands():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM brands ORDER BY sort_order ASC, created_at DESC')
    brands = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(brands)

@app.route('/api/brands', methods=['POST'])
def create_brand():
    # 支持JSON和表单数据
    if request.content_type and 'application/json' in request.content_type:
        data = request.json
        logo_url = data.get('logo')
    else:
        data = request.form.to_dict()
        logo_url = data.get('logo')
        # 如果有文件上传
        if 'logo_file' in request.files:
            file = request.files['logo_file']
            if file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                logo_url = f"/api/uploads/{filename}"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO brands (name, logo) VALUES (?, ?)
    ''', (data['name'], logo_url))
    conn.commit()
    brand_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': brand_id, 'message': 'Brand created successfully'}), 201

@app.route('/api/brands/<int:brand_id>', methods=['PUT'])
def update_brand(brand_id):
    # 支持JSON和表单数据
    if request.content_type and 'application/json' in request.content_type:
        data = request.json
        logo_url = data.get('logo')
    else:
        data = request.form.to_dict()
        logo_url = data.get('logo')
        # 如果有文件上传
        if 'logo_file' in request.files:
            file = request.files['logo_file']
            if file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                logo_url = f"/api/uploads/{filename}"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE brands SET name = ?, logo = ? WHERE id = ?
    ''', (data['name'], logo_url, brand_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Brand updated successfully'})

@app.route('/api/brands/<int:brand_id>', methods=['DELETE'])
def delete_brand(brand_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM brands WHERE id = ?', (brand_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Brand deleted successfully'})

@app.route('/api/brands/reorder', methods=['POST'])
def reorder_brands():
    """批量更新品牌排序"""
    data = request.json
    brand_orders = data.get('orders', [])  # [{id: 1, sort_order: 0}, {id: 2, sort_order: 1}, ...]
    
    conn = get_db()
    cursor = conn.cursor()
    for item in brand_orders:
        cursor.execute('UPDATE brands SET sort_order = ? WHERE id = ?', (item['sort_order'], item['id']))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Brand order updated successfully'})

# ==================== 加速卡管理 ====================

@app.route('/api/brands/<int:brand_id>/accelerators', methods=['GET'])
def get_accelerators(brand_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM accelerator_cards WHERE brand_id = ? ORDER BY created_at DESC', (brand_id,))
    accelerators = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(accelerators)

@app.route('/api/brands/<int:brand_id>/accelerators', methods=['POST'])
def create_accelerator(brand_id):
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO accelerator_cards 
        (brand_id, name, model, memory)
        VALUES (?, ?, ?, ?)
    ''', (brand_id, data['name'], data['model'], data.get('memory')))
    conn.commit()
    accelerator_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': accelerator_id, 'message': 'Accelerator created successfully'}), 201

@app.route('/api/accelerators/<int:accelerator_id>', methods=['PUT'])
def update_accelerator(accelerator_id):
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE accelerator_cards 
        SET name = ?, model = ?, memory = ?
        WHERE id = ?
    ''', (data['name'], data['model'], data.get('memory'), accelerator_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Accelerator updated successfully'})

@app.route('/api/accelerators/<int:accelerator_id>', methods=['DELETE'])
def delete_accelerator(accelerator_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM accelerator_cards WHERE id = ?', (accelerator_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Accelerator deleted successfully'})

# ==================== 模型管理 ====================

@app.route('/api/models', methods=['GET'])
def get_models():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM models ORDER BY created_at DESC')
    models = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(models)

@app.route('/api/models', methods=['POST'])
def create_model():
    # 支持JSON和表单数据
    if request.content_type and 'application/json' in request.content_type:
        data = request.json
        logo_url = data.get('logo')
    else:
        data = request.form.to_dict()
        logo_url = data.get('logo')
        # 如果有文件上传
        if 'logo_file' in request.files:
            file = request.files['logo_file']
            if file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                logo_url = f"/api/uploads/{filename}"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('INSERT INTO models (name, logo, parameters) VALUES (?, ?, ?)', 
                   (data['name'], logo_url, data.get('parameters')))
    conn.commit()
    model_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': model_id, 'message': 'Model created successfully'}), 201

@app.route('/api/models/<int:model_id>', methods=['PUT'])
def update_model(model_id):
    # 支持JSON和表单数据
    if request.content_type and 'application/json' in request.content_type:
        data = request.json
        logo_url = data.get('logo')
    else:
        data = request.form.to_dict()
        logo_url = data.get('logo')
        # 如果有文件上传
        if 'logo_file' in request.files:
            file = request.files['logo_file']
            if file.filename and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                logo_url = f"/api/uploads/{filename}"
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE models SET name = ?, logo = ?, parameters = ? WHERE id = ?', 
                   (data['name'], logo_url, data.get('parameters'), model_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Model updated successfully'})

@app.route('/api/models/<int:model_id>', methods=['DELETE'])
def delete_model(model_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM models WHERE id = ?', (model_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Model deleted successfully'})

# ==================== 服务器管理 ====================

@app.route('/api/servers', methods=['GET'])
def get_servers():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, b.name as brand_name, ac.name as model_name 
        FROM servers s
        LEFT JOIN brands b ON s.brand_id = b.id
        LEFT JOIN accelerator_cards ac ON s.model_id = ac.id
        ORDER BY s.created_at DESC
    ''')
    servers = []
    for row in cursor.fetchall():
        server = dict(row)
        servers.append(server)
    conn.close()
    return jsonify(servers)

@app.route('/api/servers', methods=['POST'])
def create_server():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO servers (name, brand_id, model_id, host_ip, port, username, password)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (data['name'], data.get('brand_id'), data.get('model_id'), 
          data['host_ip'], data.get('port', 22), data['username'], data.get('password')))
    conn.commit()
    server_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': server_id, 'message': 'Server created successfully'}), 201

@app.route('/api/servers/<int:server_id>', methods=['PUT'])
def update_server(server_id):
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE servers 
        SET name = ?, brand_id = ?, model_id = ?, host_ip = ?, port = ?, username = ?, password = ?
        WHERE id = ?
    ''', (data['name'], data.get('brand_id'), data.get('model_id'), data['host_ip'], 
          data.get('port', 22), data['username'], data.get('password'), server_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Server updated successfully'})

@app.route('/api/servers/<int:server_id>', methods=['DELETE'])
def delete_server(server_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM servers WHERE id = ?', (server_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Server deleted successfully'})

def ping_host(host):
    """使用ping命令检查主机是否在线"""
    try:
        # 根据操作系统选择ping命令参数
        if platform.system().lower() == 'windows':
            # Windows: -n 1 表示发送1个包, -w 1000 表示超时1000ms
            result = subprocess.run(['ping', '-n', '1', '-w', '1000', host], 
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        else:
            # Linux/Mac: -c 1 表示发送1个包, -W 1 表示超时1秒
            result = subprocess.run(['ping', '-c', '1', '-W', '1', host], 
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        return result.returncode == 0
    except:
        return False

@app.route('/api/servers/<int:server_id>/check', methods=['POST'])
def check_server(server_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
    server = dict(cursor.fetchone())
    conn.close()
    
    # 使用ping检查主机是否在线
    status = 'online' if ping_host(server['host_ip']) else 'offline'
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE servers SET status = ?, last_check = ? WHERE id = ?', 
                   (status, datetime.now(), server_id))
    conn.commit()
    conn.close()
    
    return jsonify({'status': status})

def get_server_resources(server):
    """通过SSH获取服务器资源使用情况"""
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(server['host_ip'], port=server['port'], 
                   username=server['username'], password=server.get('password'),
                   timeout=5)
        
        resources = {
            'cpu_usage': 0.0,
            'memory_usage': 0.0,
            'disk_usage': 0.0
        }
        
        # 获取CPU使用率
        try:
            # 使用更可靠的方法获取CPU使用率
            stdin, stdout, stderr = ssh.exec_command("grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$3+$4+$5)} END {print usage}'")
            cpu_output = stdout.read().decode().strip()
            if cpu_output:
                resources['cpu_usage'] = round(float(cpu_output), 1)
            else:
                # 备用方法：使用top命令
                stdin, stdout, stderr = ssh.exec_command("top -bn1 | grep 'Cpu(s)' | sed 's/.*, *\\([0-9.]*\\)%* id.*/\\1/' | awk '{print 100 - $1}'")
                cpu_output = stdout.read().decode().strip()
                if cpu_output:
                    resources['cpu_usage'] = round(float(cpu_output), 1)
        except:
            pass
        
        # 获取内存使用率
        try:
            stdin, stdout, stderr = ssh.exec_command("free | grep Mem | awk '{printf \"%.1f\", $3/$2 * 100}'")
            mem_output = stdout.read().decode().strip()
            if mem_output:
                resources['memory_usage'] = round(float(mem_output), 1)
        except:
            pass
        
        # 获取磁盘使用率
        try:
            stdin, stdout, stderr = ssh.exec_command("df -h / | tail -1 | awk '{print $5}' | sed 's/%//'")
            disk_output = stdout.read().decode().strip()
            if disk_output:
                resources['disk_usage'] = round(float(disk_output), 1)
        except:
            pass
        
        ssh.close()
        return resources
    except Exception as e:
        return {
            'cpu_usage': None,
            'memory_usage': None,
            'disk_usage': None,
            'error': str(e)
        }

@app.route('/api/servers/<int:server_id>/resources', methods=['GET'])
def get_server_resources_api(server_id):
    """获取服务器资源使用情况"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
    server = dict(cursor.fetchone())
    conn.close()
    
    resources = get_server_resources(server)
    return jsonify(resources)

@app.route('/api/servers/resources', methods=['POST'])
def get_all_servers_resources():
    """批量获取所有服务器的资源使用情况"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM servers')
    servers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    results = []
    for server in servers:
        resources = get_server_resources(server)
        results.append({
            'id': server['id'],
            **resources
        })
    
    return jsonify(results)

@app.route('/api/servers/check-all', methods=['POST'])
def check_all_servers():
    """批量检查所有服务器状态"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM servers')
    servers = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    results = []
    for server in servers:
        status = 'online' if ping_host(server['host_ip']) else 'offline'
        
        # 更新数据库
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE servers SET status = ?, last_check = ? WHERE id = ?', 
                      (status, datetime.now(), server['id']))
        conn.commit()
        conn.close()
        
        results.append({'id': server['id'], 'status': status})
    
    return jsonify({'results': results})

# ==================== 服务管理 ====================

def call_service_agent(server, endpoint, method='GET', data=None, timeout=30):
    """
    调用服务代理接口
    server: 服务器信息字典
    endpoint: 接口路径，如 '/service/1/deploy'
    method: HTTP方法
    data: 请求数据
    timeout: 超时时间（秒）
    """
    try:
        agent_port = server.get('agent_port', 8888)
        url = f"http://{server['host_ip']}:{agent_port}{endpoint}"
        
        if method == 'GET':
            response = requests.get(url, timeout=timeout)
        elif method == 'POST':
            response = requests.post(url, json=data, timeout=timeout)
        else:
            return {'error': f'Unsupported method: {method}'}
        
        if response.status_code == 200:
            return response.json()
        else:
            return {'error': f'HTTP {response.status_code}: {response.text}'}
    except requests.exceptions.RequestException as e:
        return {'error': f'连接失败: {str(e)}'}
    except Exception as e:
        return {'error': str(e)}

def update_service_status(service_id):
    """更新服务状态：离线、在线、启动中、服务中、关闭中"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM services WHERE id = ?', (service_id,))
        service = dict(cursor.fetchone())
        conn.close()
        
        if not service:
            return
        
        current_status = service.get('deploy_status')
        server_ids = json.loads(service['server_ids']) if service['server_ids'] else []
        
        # 如果状态是启动中，不更新（保持当前状态，等待启动完成）
        if current_status == '启动中':
            return
        
        # 如果状态是关闭中，仍然需要检查agent是否真的存在
        # 如果agent进程已被杀死，应该更新为离线状态
        # 如果agent存在但服务已停止，应该更新为在线状态
        if not server_ids:
            # 没有服务器，状态为离线
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', ('离线', service_id))
            conn.commit()
            conn.close()
            return
        
        # 获取第一个服务器的状态（简化处理，实际可以聚合多个服务器状态）
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_ids[0],))
        server = dict(cursor.fetchone())
        conn.close()
        
        # 先检查服务器本身是否在线
        server_online = ping_host(server['host_ip'])

        if not server_online:
            # 服务器离线
            db_status = '离线'
        else:
            # 服务器在线，检查服务状态
            result = call_service_agent(server, f'/service/{service_id}/status', 'GET', timeout=5)
        
        if 'error' in result:
                # 无法访问agent服务，状态为离线
                db_status = '离线'
        else:
                # 根据agent返回的状态映射
            agent_status = result.get('status', 'stopped')
            if agent_status == 'running':
                    db_status = '服务中'  # 服务正在运行
            else:
                    db_status = '在线'  # 服务器在线但服务未运行
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', (db_status, service_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'Error updating service status: {e}')
        # 出错时设为离线
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', ('离线', service_id))
            conn.commit()
            conn.close()
        except:
            pass

def execute_ssh_command(server, command):
    """
    执行SSH命令的改进版本
    使用伪终端和完整的环境变量加载
    """
    ssh = None
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(server['host_ip'], port=server['port'], 
                   username=server['username'], password=server.get('password'),
                   timeout=30)
        
        # 使用here document创建完整的登录shell环境
        # bash -l 会加载所有登录环境变量
        # 使用<< 'EOF' 确保命令中的变量不会被提前展开
        full_command = f"""bash -l << 'EOF'
{command}
EOF"""

        print(f"[DEBUG] 执行SSH命令: {command}")
        
        stdin, stdout, stderr = ssh.exec_command(full_command, get_pty=True)
        
        # 等待命令执行完成
        exit_status = stdout.channel.recv_exit_status()
        
        # 读取所有输出
        output = stdout.read().decode('utf-8', errors='ignore')
        error = stderr.read().decode('utf-8', errors='ignore')
        
        return {
            'success': exit_status == 0,
            'output': output,
            'error': error,
            'exit_status': exit_status
        }
    except Exception as e:
        return {
            'success': False,
            'output': '',
            'error': str(e),
            'exit_status': -1
        }
    finally:
        if ssh:
            ssh.close()

def deploy_service_thread(service_id, server_ids, deploy_command):
    """在后台线程中执行部署命令"""
    try:
        # 更新状态为启动中
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', ('启动中', service_id))
        conn.commit()
        conn.close()
        
        # 获取服务器信息
        conn = get_db()
        cursor = conn.cursor()
        servers = []
        for server_id in server_ids:
            cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
            server = dict(cursor.fetchone())
            servers.append(server)
        conn.close()
        
        # 在每个服务器上调用客户端接口部署
        all_success = True
        deploy_results = []
        for server in servers:
            server_result = {
                'server_id': server['id'],
                'server_name': server['name'],
                'server_ip': server['host_ip'],
                'success': False,
                'output': '',
                'error': ''
            }
            try:
                result = call_service_agent(server, f'/service/{service_id}/deploy', 'POST', {
                    'command': deploy_command
                })
                if 'error' in result:
                    server_result['error'] = result['error']
                    all_success = False
                else:
                    server_result['success'] = True
                    server_result['output'] = result.get('message', '部署成功')
            except Exception as e:
                server_result['error'] = str(e)
                all_success = False
            
            deploy_results.append(server_result)
        
        # 更新部署状态和结果
        conn = get_db()
        cursor = conn.cursor()
        # 部署完成后，根据部署结果设置状态
        all_success = all(result.get('success', False) for result in deploy_results)
        if all_success:
            # 部署成功，设置为服务中（等待状态检查确认）
            status = '服务中'
        else:
            # 部署失败，设置为在线（服务器在线但服务部署失败）
            status = '在线'
        result_json = json.dumps(deploy_results, ensure_ascii=False)
        cursor.execute('UPDATE services SET deploy_status = ?, deploy_result = ? WHERE id = ?', 
                      (status, result_json, service_id))
        conn.commit()
        conn.close()
        
        # 部署完成后立即检查一次状态
        update_service_status(service_id)
    except Exception as e:
        # 部署失败，设为离线
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', ('离线', service_id))
        conn.commit()
        conn.close()

@app.route('/api/services', methods=['GET'])
def get_services():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.name as model_name 
        FROM services s
        LEFT JOIN models m ON s.model_id = m.id
        ORDER BY s.created_at DESC
    ''')
    services = []
    for row in cursor.fetchall():
        service = dict(row)
        service['server_ids'] = json.loads(service['server_ids']) if service['server_ids'] else []
        services.append(service)
    conn.close()
    
    # 自动更新所有服务状态（不阻塞响应）
    def update_all_statuses():
        for service in services:
            try:
                update_service_status(service['id'])
            except:
                pass
    
    threading.Thread(target=update_all_statuses, daemon=True).start()
    
    return jsonify(services)

@app.route('/api/services/refresh-status', methods=['POST'])
def refresh_services_status():
    """手动刷新所有服务状态"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.name as model_name 
        FROM services s
        LEFT JOIN models m ON s.model_id = m.id
        ORDER BY s.created_at DESC
    ''')
    services = []
    for row in cursor.fetchall():
        service = dict(row)
        service['server_ids'] = json.loads(service['server_ids']) if service['server_ids'] else []
        services.append(service)
    conn.close()
    
    # 立即更新所有服务状态（同步执行）
    for service in services:
        try:
            update_service_status(service['id'])
        except:
            pass
    
    # 重新获取更新后的服务列表
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, m.name as model_name 
        FROM services s
        LEFT JOIN models m ON s.model_id = m.id
        ORDER BY s.created_at DESC
    ''')
    updated_services = []
    for row in cursor.fetchall():
        service = dict(row)
        service['server_ids'] = json.loads(service['server_ids']) if service['server_ids'] else []
        updated_services.append(service)
    conn.close()
    
    return jsonify(updated_services)

@app.route('/api/services', methods=['POST'])
def create_service():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    server_ids = json.dumps(data.get('server_ids', []))
    deploy_command = data.get('deploy_command', '')
    cursor.execute('''
        INSERT INTO services (name, model_id, server_ids, deploy_command, deploy_status)
        VALUES (?, ?, ?, ?, ?)
    ''', (data['name'], data['model_id'], server_ids, deploy_command, '离线'))
    conn.commit()
    service_id = cursor.lastrowid
    conn.close()
    
    # 如果有部署命令，调用客户端接口部署
    if deploy_command and data.get('server_ids'):
        threading.Thread(target=deploy_service_thread, args=(service_id, data['server_ids'], deploy_command), daemon=True).start()
    
    return jsonify({'id': service_id, 'message': 'Service created successfully'}), 201

@app.route('/api/services/<int:service_id>', methods=['PUT'])
def update_service(service_id):
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    server_ids = json.dumps(data.get('server_ids', []))
    deploy_command = data.get('deploy_command', '')
    cursor.execute('''
        UPDATE services SET name = ?, model_id = ?, server_ids = ?, deploy_command = ? WHERE id = ?
    ''', (data['name'], data['model_id'], server_ids, deploy_command, service_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Service updated successfully'})

@app.route('/api/services/<int:service_id>/restart', methods=['POST'])
def restart_service(service_id):
    """重启服务"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM services WHERE id = ?', (service_id,))
    service = dict(cursor.fetchone())
    conn.close()
    
    if not service:
        return jsonify({'error': 'Service not found'}), 404
    
    server_ids = json.loads(service['server_ids']) if service['server_ids'] else []
    if not server_ids:
        return jsonify({'error': 'No servers associated with this service'}), 400
    
    # 在开始重启前立即设置状态为启动中
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', ('启动中', service_id))
    conn.commit()
    conn.close()

    # 获取服务器信息
    conn = get_db()
    cursor = conn.cursor()
    servers = []
    for server_id in server_ids:
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
        server = dict(cursor.fetchone())
        servers.append(server)
    conn.close()
    
    # 执行重启命令（使用部署命令，但添加重启逻辑）
    # 这里假设重启命令就是在部署命令前添加重启逻辑，或者使用相同的命令
    # 实际使用中，可以添加专门的重启命令字段，这里简化处理使用部署命令
    restart_command = service.get('deploy_command', '')
    if not restart_command:
        return jsonify({'error': 'No deploy command found'}), 400
    
    # 在后台线程中执行重启
    def restart_thread():
        all_success = True
        deploy_results = []
        for server in servers:
            server_result = {
                'server_id': server['id'],
                'server_name': server['name'],
                'server_ip': server['host_ip'],
                'success': False,
                'output': '',
                'error': ''
            }
            try:
                result = call_service_agent(server, f'/service/{service_id}/restart', 'POST', {
                    'command': restart_command
                })
                if 'error' in result:
                    server_result['error'] = result['error']
                    all_success = False
                else:
                    server_result['success'] = True
                    server_result['output'] = result.get('message', '重启成功')
            except Exception as e:
                server_result['error'] = str(e)
                all_success = False
            
            deploy_results.append(server_result)
        
        # 更新部署状态和结果
        conn = get_db()
        cursor = conn.cursor()
        # 重启完成后，根据重启结果设置状态
        all_success = all(result.get('success', False) for result in deploy_results)
        if all_success:
            # 重启成功，设置为服务中（等待状态检查确认）
            status = '服务中'
        else:
            # 重启失败，设置为在线（服务器在线但服务重启失败）
            status = '在线'
        result_json = json.dumps(deploy_results, ensure_ascii=False)
        cursor.execute('UPDATE services SET deploy_status = ?, deploy_result = ? WHERE id = ?', 
                      (status, result_json, service_id))
        conn.commit()
        conn.close()
        
        # 重启完成后立即检查一次状态
        update_service_status(service_id)
    
    threading.Thread(target=restart_thread, daemon=True).start()
    
    return jsonify({'message': 'Service restart initiated'})

@app.route('/api/services/<int:service_id>/stop', methods=['POST'])
def stop_service(service_id):
    """停止服务"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM services WHERE id = ?', (service_id,))
    service = dict(cursor.fetchone())
    conn.close()
    
    if not service:
        return jsonify({'error': 'Service not found'}), 404
    
    server_ids = json.loads(service['server_ids']) if service['server_ids'] else []
    if not server_ids:
        return jsonify({'error': 'No servers associated with this service'}), 400
    
    # 获取服务器信息
    conn = get_db()
    cursor = conn.cursor()
    servers = []
    for server_id in server_ids:
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
        server = dict(cursor.fetchone())
        servers.append(server)
    conn.close()
    
    # 设置状态为关闭中
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', ('关闭中', service_id))
    conn.commit()
    conn.close()
    
    # 在后台线程中执行停止
    def stop_thread():
        all_success = True
        stop_results = []
        agent_unavailable = False  # 标记agent是否不可用
        
        for server in servers:
            server_result = {
                'server_id': server['id'],
                'server_name': server['name'],
                'server_ip': server['host_ip'],
                'success': False,
                'message': ''
            }
            try:
                result = call_service_agent(server, f'/service/{service_id}/stop', 'POST')
                if 'error' in result:
                    server_result['message'] = result['error']
                    all_success = False
                    # 检查是否是agent不可用的错误
                    error_msg = result['error'].lower()
                    if '连接失败' in result['error'] or 'connection' in error_msg or 'timeout' in error_msg or 'refused' in error_msg:
                        agent_unavailable = True
                else:
                    server_result['success'] = True
                    server_result['message'] = result.get('message', '停止成功')
            except Exception as e:
                server_result['message'] = str(e)
                all_success = False
                # 检查是否是连接错误（agent进程不存在）
                error_str = str(e).lower()
                if 'connection' in error_str or 'timeout' in error_str or 'refused' in error_str or '连接' in str(e):
                    agent_unavailable = True
            
            stop_results.append(server_result)
        
        # 更新部署状态
        conn = get_db()
        cursor = conn.cursor()
        # 如果agent不可用，状态设为离线；否则设为在线（服务器在线但服务已停止）
        status = '离线' if agent_unavailable else '在线'
        cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', (status, service_id))
        conn.commit()
        conn.close()
        
        # 停止后立即检查一次状态（即使agent不可用也检查，确保状态正确）
        update_service_status(service_id)
    
    threading.Thread(target=stop_thread, daemon=True).start()
    
    return jsonify({'message': 'Service stop initiated'})


@app.route('/api/services/<int:service_id>/stop-agent', methods=['POST'])
def stop_service_agent(service_id):
    """完全停止服务代理"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM services WHERE id = ?', (service_id,))
    service = dict(cursor.fetchone())
    conn.close()

    if not service:
        return jsonify({'error': 'Service not found'}), 404

    server_ids = json.loads(service['server_ids']) if service['server_ids'] else []
    if not server_ids:
        return jsonify({'error': 'No servers associated with this service'}), 400

    # 获取第一个服务器的信息
    conn = get_db()
    cursor = conn.cursor()
    servers = []
    for server_id in server_ids:
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
        server = dict(cursor.fetchone())
        servers.append(server)
    conn.close()

    # 在后台线程中执行完全停止
    def stop_agent_thread():
        all_success = True
        stop_results = []
        for server in servers:
            server_result = {
                'server_id': server['id'],
                'server_name': server['name'],
                'server_ip': server['host_ip'],
                'success': False,
                'message': ''
            }
            try:
                # 调用agent的stop-agent接口
                result = call_service_agent(server, f'/service/{service_id}/stop-agent', 'POST')
                if 'error' in result:
                    server_result['message'] = result['error']
                    all_success = False
                else:
                    server_result['success'] = True
                    server_result['message'] = result.get('message', '代理停止成功')
            except Exception as e:
                server_result['message'] = str(e)
                all_success = False

            stop_results.append(server_result)

        # 更新服务状态为离线
        conn = get_db()
        cursor = conn.cursor()
        status = '离线'  # 代理停止后，状态设为离线
        cursor.execute('UPDATE services SET deploy_status = ? WHERE id = ?', (status, service_id))
        conn.commit()
        conn.close()

    threading.Thread(target=stop_agent_thread, daemon=True).start()

    return jsonify({'message': 'Service agent stop initiated'})

@app.route('/api/services/<int:service_id>/deploy-log', methods=['GET'])
def get_deploy_log(service_id):
    """获取服务部署日志"""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT deploy_result FROM services WHERE id = ?', (service_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'error': 'Service not found'}), 404
    
    deploy_result = row['deploy_result']
    if deploy_result:
        try:
            results = json.loads(deploy_result)
            return jsonify(results)
        except:
            return jsonify([])
    return jsonify([])

@app.route('/api/services/<int:service_id>', methods=['DELETE'])
def delete_service(service_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM services WHERE id = ?', (service_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Service deleted successfully'})

# ==================== 聊天记录 ====================

@app.route('/api/services/<int:service_id>/chat', methods=['GET'])
def get_chat_history(service_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT * FROM chat_history WHERE service_id = ? ORDER BY created_at ASC
    ''', (service_id,))
    messages = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(messages)

@app.route('/api/services/<int:service_id>/chat', methods=['POST'])
def add_chat_message(service_id):
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO chat_history (service_id, role, content)
        VALUES (?, ?, ?)
    ''', (service_id, data['role'], data['content']))
    conn.commit()
    message_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': message_id, 'message': 'Message added successfully'}), 201

@app.route('/api/services/<int:service_id>/chat', methods=['DELETE'])
def clear_chat_history(service_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM chat_history WHERE service_id = ?', (service_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Chat history cleared successfully'})

@app.route('/api/services/<int:service_id>/chat/completions', methods=['POST'])
def chat_completions(service_id):
    """调用大模型API（代理到目标服务器的8000端口）"""
    try:
        # 获取服务信息
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM services WHERE id = ?', (service_id,))
        service = dict(cursor.fetchone())
        conn.close()
        
        if not service:
            return jsonify({'error': 'Service not found'}), 404
        
        # 获取服务器的IP地址
        server_ids = json.loads(service['server_ids']) if service['server_ids'] else []
        if not server_ids:
            return jsonify({'error': 'No servers associated with this service'}), 400
        
        # 获取第一个服务器的信息
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_ids[0],))
        server = dict(cursor.fetchone())
        conn.close()
        
        if not server:
            return jsonify({'error': 'Server not found'}), 404
        
        # 构建目标URL
        # 改为统一走目标服务器的 service_agent（agent_port，默认8888）做代理，
        # 这样推理服务端口可以动态分配，避免 8889/8000 等端口冲突。
        server_ip = server['host_ip']
        agent_port = server.get('agent_port', 8888)
        target_url = f"http://{server_ip}:{agent_port}/service/{service_id}/chat/completions"
        
        # 获取请求数据
        request_data = request.json or {}
        
        # 设置默认值
        if 'model' not in request_data:
            request_data['model'] = 'jiuge'
        
        # 转发请求到目标服务器
        # 注意：流式场景需要显式声明 Accept: text/event-stream，且下游/中间层可能会缓冲
        is_stream = bool(request_data.get('stream', False))
        headers = {'Content-Type': 'application/json'}
        if is_stream:
            headers['Accept'] = 'text/event-stream'
            headers['Cache-Control'] = 'no-cache'
            headers['Accept-Encoding'] = 'identity'

        response = requests.post(
            target_url,
            json=request_data,
            headers=headers,
            timeout=120,
            stream=is_stream
        )
        
        # 如果是流式响应，需要特殊处理
        if is_stream:
            # 关键点：不要用 iter_lines 重新组装SSE（容易被缓冲/换行影响）
            # 直接把上游推理服务的 bytes 原样转发给前端，保证前端能持续读到增量数据块
            from flask import Response, stream_with_context

            def generate():
                try:
                    for chunk in response.iter_content(chunk_size=None):
                        if chunk:
                            yield chunk
                except Exception as e:
                    print(f'流式响应错误: {e}')
                    import traceback
                    traceback.print_exc()
                    error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
                    yield f'data: {error_data}\n\n'.encode('utf-8')
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
                    'X-Accel-Buffering': 'no'  # 禁用nginx缓冲
                }
            )
        else:
            # 非流式响应，直接返回JSON
            if response.status_code == 200:
                return jsonify(response.json())
            else:
                return jsonify({'error': f'API request failed: {response.text}'}), response.status_code
                
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Connection failed: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== 计划任务 ====================

@app.route('/api/tasks', methods=['GET'])
def get_tasks():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.*, s.name as server_name 
        FROM tasks t
        LEFT JOIN servers s ON t.server_id = s.id
        ORDER BY t.created_at DESC
    ''')
    tasks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify(tasks)

@app.route('/api/tasks', methods=['POST'])
def create_task():
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO tasks (name, command, server_id, schedule_type)
        VALUES (?, ?, ?, ?)
    ''', (data['name'], data['command'], data['server_id'], data['schedule_type']))
    conn.commit()
    task_id = cursor.lastrowid
    conn.close()
    return jsonify({'id': task_id, 'message': 'Task created successfully'}), 201

@app.route('/api/tasks/<int:task_id>', methods=['PUT'])
def update_task(task_id):
    data = request.json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE tasks SET name = ?, command = ?, server_id = ?, schedule_type = ? WHERE id = ?
    ''', (data['name'], data['command'], data['server_id'], data['schedule_type'], task_id))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Task updated successfully'})

@app.route('/api/tasks/<int:task_id>', methods=['DELETE'])
def delete_task(task_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Task deleted successfully'})

@app.route('/api/tasks/<int:task_id>/execute', methods=['POST'])
def execute_task(task_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT t.*, s.* FROM tasks t JOIN servers s ON t.server_id = s.id WHERE t.id = ?', (task_id,))
    task_row = cursor.fetchone()
    if not task_row:
        return jsonify({'error': 'Task not found'}), 404
    
    task = dict(task_row)
    conn.close()
    
    # 更新任务状态
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE tasks SET status = ?, last_run = ? WHERE id = ?', 
                   ('executing', datetime.now(), task_id))
    conn.commit()
    conn.close()
    
    # 执行任务（这里简化处理，实际应该使用后台任务队列）
    try:
        result = execute_ssh_command(task, task['command'])
        result_data = {
            'output': result['output'], 
            'error': result['error'], 
            'success': result['success']
        }
        result_json = json.dumps(result_data, ensure_ascii=False)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE tasks SET status = ?, result = ? WHERE id = ?', 
                      ('completed', result_json, task_id))
        conn.commit()
        conn.close()
        
        return jsonify(result_data)
    except Exception as e:
        error_message = str(e)
        result_data = {'output': '', 'error': error_message, 'success': False}
        result_json = json.dumps(result_data, ensure_ascii=False)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('UPDATE tasks SET status = ?, result = ? WHERE id = ?', 
                      ('failed', result_json, task_id))
        conn.commit()
        conn.close()
        return jsonify(result_data), 500

@app.route('/api/tasks/<int:task_id>/result', methods=['GET'])
def get_task_result(task_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT result, last_run FROM tasks WHERE id = ?', (task_id,))
    task_row = cursor.fetchone()
    conn.close()
    
    if not task_row:
        return jsonify({'error': 'Task not found'}), 404
    
    result_str = task_row['result']
    last_run = task_row['last_run']
    
    response_data = {
        'last_run': last_run
    }
    
    if result_str:
        try:
            result_data = json.loads(result_str)
            response_data.update(result_data)
            return jsonify(response_data)
        except json.JSONDecodeError:
            return jsonify({'error': 'Invalid result data', 'raw': result_str, 'last_run': last_run}), 500
    else:
        response_data['message'] = 'No result available yet'
        return jsonify(response_data)
    return jsonify({'message': 'Task result stored in database'})

# ==================== 统计信息 ====================

@app.route('/api/stats', methods=['GET'])
def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as count FROM servers')
    server_count = dict(cursor.fetchone())['count']
    
    cursor.execute('SELECT COUNT(*) as count FROM services')
    service_count = dict(cursor.fetchone())['count']
    
    cursor.execute('SELECT COUNT(*) as count FROM servers WHERE status = ?', ('online',))
    online_server_count = dict(cursor.fetchone())['count']
    
    conn.close()
    return jsonify({
        'server_count': server_count,
        'service_count': service_count,
        'online_server_count': online_server_count
    })

# ==================== SSH WebSocket终端 ====================

@socketio.on('ssh_connect')
def handle_ssh_connect(data):
    server_id = data['server_id']
    auto_command = data.get('auto_command', None)  # 可选：自动执行的命令
    service_id = data.get('service_id', None)  # 服务ID，用于持久化连接
    session_id = request.sid
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
    server = dict(cursor.fetchone())
    conn.close()
    
    try:
        # 如果是服务连接，检查是否已有持久化连接
        if service_id and service_id in service_ssh_connections:
            # 复用已有连接
            conn_info = service_ssh_connections[service_id]
            conn_info['sessions'].append(session_id)
            
            # 创建新的channel用于此会话（每个会话需要独立的channel）
            chan = conn_info['ssh'].invoke_shell(term='xterm-256color')
            chan.settimeout(0.1)
            
            ssh_connections[session_id] = {
                'ssh': conn_info['ssh'],
                'chan': chan,
                'server_id': server_id,
                'service_id': service_id,
                'persistent': True
            }
            
            emit('ssh_connected', {'status': 'connected', 'reused': True})
            
            # 启动接收线程
            threading.Thread(target=ssh_receive_thread, args=(session_id,), daemon=True).start()
        else:
            # 创建新连接
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server['host_ip'], port=server['port'], 
                       username=server['username'], password=server.get('password'))
            
            chan = ssh.invoke_shell(term='xterm-256color')
            chan.settimeout(0.1)
            
            ssh_connections[session_id] = {
                'ssh': ssh,
                'chan': chan,
                'server_id': server_id,
                'service_id': service_id,
                'persistent': service_id is not None
            }
            
            # 如果是服务连接，保存到持久化连接池
            if service_id:
                service_ssh_connections[service_id] = {
                    'ssh': ssh,
                    'chan': chan,  # 主channel
                    'server_id': server_id,
                    'sessions': [session_id]
                }
            
            emit('ssh_connected', {'status': 'connected'})
            
            # 如果有自动执行的命令，等待shell准备就绪后执行
            if auto_command:
                # 等待shell准备就绪（通常需要等待一下）
                time.sleep(0.5)
                chan.send(auto_command + '\r\n')
            
            # 启动接收线程
            threading.Thread(target=ssh_receive_thread, args=(session_id,), daemon=True).start()
    except Exception as e:
        emit('ssh_error', {'error': str(e)})

def ssh_receive_thread(session_id):
    if session_id not in ssh_connections:
        return
    
    chan = ssh_connections[session_id]['chan']
    
    try:
        while session_id in ssh_connections:
            try:
                if chan.recv_ready():
                    data = chan.recv(4096)
                    socketio.emit('ssh_output', {'data': data.decode('utf-8', errors='ignore')}, room=session_id)
                else:
                    time.sleep(0.05)  # 减少延迟以提高响应速度
            except paramiko.ssh_exception.SSHException:
                break
            except Exception as e:
                if session_id in ssh_connections:
                    socketio.emit('ssh_error', {'error': str(e)}, room=session_id)
                break
    except Exception:
        pass
    finally:
        # 清理连接
        if session_id in ssh_connections:
            try:
                ssh_connections[session_id]['chan'].close()
                ssh_connections[session_id]['ssh'].close()
            except:
                pass
            del ssh_connections[session_id]

@socketio.on('ssh_input')
def handle_ssh_input(data):
    session_id = request.sid
    if session_id in ssh_connections:
        try:
            ssh_connections[session_id]['chan'].send(data['input'])
        except:
            pass

@socketio.on('ssh_resize')
def handle_ssh_resize(data):
    """处理终端尺寸调整"""
    session_id = request.sid
    if session_id in ssh_connections:
        try:
            chan = ssh_connections[session_id]['chan']
            cols = data.get('cols', 80)
            rows = data.get('rows', 24)
            # 使用paramiko的resize_pty方法调整终端尺寸
            chan.resize_pty(width=cols, height=rows)
        except Exception as e:
            print(f'Error resizing terminal: {e}')
            pass

@socketio.on('ssh_disconnect')
def handle_ssh_disconnect():
    session_id = request.sid
    if session_id in ssh_connections:
        conn_info = ssh_connections[session_id]
        service_id = conn_info.get('service_id')
        is_persistent = conn_info.get('persistent', False)
        
        try:
            # 只关闭这个会话的channel
            conn_info['chan'].close()
            
            # 如果是持久化连接，只移除session，不断开SSH连接
            if is_persistent and service_id and service_id in service_ssh_connections:
                service_conn = service_ssh_connections[service_id]
                if session_id in service_conn['sessions']:
                    service_conn['sessions'].remove(session_id)
                # 如果所有session都断开了，可以选择保持连接或关闭
                # 这里选择保持连接，以便后续复用
            else:
                # 临时连接，完全关闭
                conn_info['ssh'].close()
        except:
            pass
        del ssh_connections[session_id]
    emit('ssh_disconnected', {'status': 'disconnected'})

@socketio.on('disconnect')
def handle_disconnect():
    session_id = request.sid
    if session_id in ssh_connections:
        conn_info = ssh_connections[session_id]
        service_id = conn_info.get('service_id')
        is_persistent = conn_info.get('persistent', False)
        
        try:
            # 只关闭这个会话的channel
            conn_info['chan'].close()
            
            # 如果是持久化连接，只移除session，不断开SSH连接
            if is_persistent and service_id and service_id in service_ssh_connections:
                service_conn = service_ssh_connections[service_id]
                if session_id in service_conn['sessions']:
                    service_conn['sessions'].remove(session_id)
            else:
                # 临时连接，完全关闭
                conn_info['ssh'].close()
        except:
            pass
        del ssh_connections[session_id]

# ==================== 跨平台测试 ====================

def call_command_agent(server, endpoint, method='POST', data=None, stream=False, timeout=300):
    """
    调用命令代理接口
    server: 服务器信息字典
    endpoint: 接口路径，如 '/command/execute'
    method: HTTP方法
    data: 请求数据
    stream: 是否流式返回
    timeout: 超时时间（秒）
    """
    try:
        # command_client 使用9090端口（与service_agent的8888区分）
        agent_port = 9090
        url = f"http://{server['host_ip']}:{agent_port}{endpoint}"
        
        if method == 'GET':
            response = requests.get(url, timeout=timeout, stream=stream)
        elif method == 'POST':
            response = requests.post(url, json=data, timeout=timeout, stream=stream)
        else:
            return {'error': f'Unsupported method: {method}'}
        
        if stream:
            return response
        else:
            if response.status_code == 200:
                return response.json()
            else:
                return {'error': f'HTTP {response.status_code}: {response.text}'}
    except requests.exceptions.RequestException as e:
        return {'error': f'连接失败: {str(e)}'}
    except Exception as e:
        return {'error': str(e)}

def execute_python_script_thread(server, script_content, session_id):
    """通过agent在后台线程中执行Python脚本并实时发送输出"""
    try:
        # 发送开始状态
        emit_func = lambda event, data: socketio.emit(event, data) if session_id.startswith('http_') else socketio.emit(event, data, room=session_id)
        
        emit_func('script_status', {
            'server_id': server['id'],
            'status': 'running'
        })
        
        # 获取该服务器的命令行参数
        command_args = server.get('command_args', '')
        
        # 调用command_client执行脚本
        response = call_command_agent(
            server,
            '/command/execute',
            method='POST',
            data={
                'script': script_content,
                'args': command_args  # 传递命令行参数
            },
            stream=True,
            timeout=300
        )
        
        if isinstance(response, dict) and 'error' in response:
            # 连接失败
            emit_func('script_error', {
                'server_id': server['id'],
                'error': response['error']
            })
            emit_func('script_status', {
                'server_id': server['id'],
                'status': 'error'
            })
            return
        
        # 处理流式响应（Server-Sent Events格式）
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                
                # SSE格式: data: {...}
                if line.startswith('data: '):
                    data_str = line[6:]  # 去掉 'data: ' 前缀
                    try:
                        data = json.loads(data_str)
                        msg_type = data.get('type', '')
                        
                        if msg_type == 'output':
                            # 输出数据
                            output_data = data.get('data', '')
                            is_error = data.get('is_error', False)
                            emit_func('script_output', {
                                'server_id': server['id'],
                                'output': output_data,
                                'is_error': is_error
                            })
                        elif msg_type == 'status':
                            # 状态更新
                            status = data.get('status', '')
                            if status in ['completed', 'error']:
                                emit_func('script_status', {
                                    'server_id': server['id'],
                                    'status': status
                                })
                                if status == 'error':
                                    return_code = data.get('return_code', -1)
                                    emit_func('script_error', {
                                        'server_id': server['id'],
                                        'error': f'脚本执行失败，退出码: {return_code}'
                                    })
                        elif msg_type == 'error':
                            # 错误信息
                            error_msg = data.get('data', '未知错误')
                            emit_func('script_error', {
                                'server_id': server['id'],
                                'error': error_msg
                            })
                            emit_func('script_status', {
                                'server_id': server['id'],
                                'status': 'error'
                            })
                    except json.JSONDecodeError:
                        # 忽略无法解析的行
                        continue
                        
        except Exception as e:
            error_msg = str(e)
            emit_func('script_error', {
                'server_id': server['id'],
                'error': f'读取输出失败: {error_msg}'
            })
            emit_func('script_status', {
                'server_id': server['id'],
                'status': 'error'
            })
        
    except Exception as e:
        error_msg = str(e)
        emit_func = lambda event, data: socketio.emit(event, data) if session_id.startswith('http_') else socketio.emit(event, data, room=session_id)
        emit_func('script_error', {
            'server_id': server['id'],
            'error': error_msg
        })
        emit_func('script_status', {
            'server_id': server['id'],
            'status': 'error'
        })

@socketio.on('run_script')
def handle_run_script(data):
    """通过SocketIO执行跨平台测试"""
    script_content = data.get('script', '')
    server_ids = data.get('server_ids', [])
    server_args = data.get('server_args', {})  # 获取每个服务器的命令行参数
    session_id = request.sid
    
    if not script_content:
        emit('script_error', {'error': '脚本内容不能为空'})
        return
    
    if not server_ids:
        emit('script_error', {'error': '请至少选择一个服务器'})
        return
    
    # 获取服务器信息
    conn = get_db()
    cursor = conn.cursor()
    servers = []
    for server_id in server_ids:
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
        row = cursor.fetchone()
        if row:
            server_dict = dict(row)
            # 为每个服务器添加对应的命令行参数
            server_dict['command_args'] = server_args.get(str(server_id), '') or server_args.get(server_id, '')
            servers.append(server_dict)
    conn.close()
    
    if len(servers) != len(server_ids):
        emit('script_error', {'error': '部分服务器不存在'})
        return
    
    # 在后台线程中为每个服务器执行脚本
    for server in servers:
        thread = threading.Thread(
            target=execute_python_script_thread,
            args=(server, script_content, session_id),
            daemon=True
        )
        thread.start()
    
    emit('script_started', {'message': '脚本已开始执行'})

@app.route('/api/cross-platform-test/run', methods=['POST'])
def run_cross_platform_test():
    """执行跨平台测试（HTTP接口，用于兼容）"""
    data = request.json
    script_content = data.get('script', '')
    server_ids = data.get('server_ids', [])
    
    if not script_content:
        return jsonify({'error': '脚本内容不能为空'}), 400
    
    if not server_ids:
        return jsonify({'error': '请至少选择一个服务器'}), 400
    
    # 获取服务器信息
    conn = get_db()
    cursor = conn.cursor()
    servers = []
    for server_id in server_ids:
        cursor.execute('SELECT * FROM servers WHERE id = ?', (server_id,))
        row = cursor.fetchone()
        if row:
            servers.append(dict(row))
    conn.close()
    
    if len(servers) != len(server_ids):
        return jsonify({'error': '部分服务器不存在'}), 404
    
    # 创建一个临时会话ID（用于HTTP请求）
    session_id = f'http_{uuid.uuid4().hex}'
    
    # 在后台线程中为每个服务器执行脚本
    for server in servers:
        thread = threading.Thread(
            target=execute_python_script_thread,
            args=(server, script_content, session_id),
            daemon=True
        )
        thread.start()
    
    return jsonify({'message': '脚本已开始执行', 'session_id': session_id})

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, host='0.0.0.0', allow_unsafe_werkzeug=True)

