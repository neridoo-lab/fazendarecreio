import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY

DB_NAME = "rebanho.db"

# Conecta ao Supabase
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def connect():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT,
        quantidade INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS movimentacoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT,
        categoria TEXT,
        sexo TEXT,
        quantidade INTEGER,
        usuario TEXT,
        data TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        sincronizado INTEGER DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        usuario TEXT UNIQUE NOT NULL,
        senha TEXT NOT NULL,
        tipo TEXT DEFAULT 'funcionario',
        ativo INTEGER DEFAULT 1,
        data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS logs_acesso (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT,
        acao TEXT,
        ip TEXT,
        data TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


def seed_categorias():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM categorias")
    if cursor.fetchone()[0] == 0:
        categorias = [
            ("Matrizes", 0),
            ("Novilhas", 0),
            ("Bezerras", 0),
            ("Touros", 0),
            ("Garrotes", 0),
            ("Bezerros", 0),
        ]
        cursor.executemany(
            "INSERT INTO categorias (nome, quantidade) VALUES (?, ?)",
            categorias
        )

    conn.commit()
    conn.close()


def seed_admin():
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE tipo = 'admin'")
    if cursor.fetchone()[0] == 0:
        senha_hash = generate_password_hash('admin123')
        cursor.execute("""
            INSERT INTO usuarios (nome, usuario, senha, tipo)
            VALUES (?, ?, ?, ?)
        """, ('Administrador', 'admin', senha_hash, 'admin'))
        print("=" * 50)
        print("✅ USUÁRIO ADMIN CRIADO COM SUCESSO!")
        print("👤 Usuário: admin")
        print("🔑 Senha: admin123")
        print("=" * 50)

    conn.commit()
    conn.close()


def verificar_usuario(usuario, senha):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nome, usuario, senha, tipo FROM usuarios WHERE usuario = ? AND ativo = 1", (usuario,))
    user = cursor.fetchone()
    conn.close()

    if user and check_password_hash(user[3], senha):
        return {
            'id': user[0],
            'nome': user[1],
            'usuario': user[2],
            'tipo': user[4]
        }
    return None


def registrar_log(usuario, acao, ip='0.0.0.0'):
    try:
        conn = connect()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs_acesso (usuario, acao, ip)
            VALUES (?, ?, ?)
        """, (usuario, acao, ip))
        conn.commit()
        conn.close()
    except:
        pass


def sincronizar_movimentacoes():
    """Envia movimentações não sincronizadas para o Supabase"""
    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, tipo, categoria, sexo, quantidade, usuario, data
        FROM movimentacoes
        WHERE sincronizado = 0
        ORDER BY id
    """)
    movimentacoes = cursor.fetchall()

    if not movimentacoes:
        conn.close()
        return 0

    enviados = 0
    for m in movimentacoes:
        try:
            data = {
                'tipo': m[1],
                'categoria': m[2],
                'sexo': m[3],
                'quantidade': m[4],
                'usuario': m[5],
                'data': m[6]
            }
            supabase.table('movimentacoes').insert(data).execute()

            cursor.execute("""
                UPDATE movimentacoes SET sincronizado = 1 WHERE id = ?
            """, (m[0],))
            enviados += 1
        except Exception as e:
            print(f"Erro ao sincronizar: {e}")

    conn.commit()
    conn.close()
    return enviados


def baixar_dados_nuvem():
    """Baixa dados do Supabase para o SQLite local"""
    try:
        response = supabase.table('categorias').select('*').execute()
        categorias_nuvem = response.data

        if categorias_nuvem:
            conn = connect()
            cursor = conn.cursor()

            for c in categorias_nuvem:
                cursor.execute("""
                    UPDATE categorias SET quantidade = ? WHERE nome = ?
                """, (c['quantidade'], c['nome']))

            conn.commit()
            conn.close()
            return True
    except Exception as e:
        print(f"Erro ao baixar dados: {e}")
        return False
    return False


def sincronizar_tudo():
    print("🔄 Iniciando sincronização...")
    enviados = sincronizar_movimentacoes()
    print(f"✅ {enviados} movimentações enviadas para a nuvem")
    if baixar_dados_nuvem():
        print("✅ Dados baixados da nuvem")
    print("✅ Sincronização concluída!")
    return enviados


def verificar_internet():
    try:
        import socket
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False


def get_all_usuarios():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, nome, usuario, tipo, ativo, data_criacao 
        FROM usuarios 
        ORDER BY id
    """)
    usuarios = cursor.fetchall()
    conn.close()
    return usuarios


def add_usuario(nome, usuario, senha, tipo='funcionario'):
    conn = connect()
    cursor = conn.cursor()
    try:
        senha_hash = generate_password_hash(senha)
        cursor.execute("""
            INSERT INTO usuarios (nome, usuario, senha, tipo)
            VALUES (?, ?, ?, ?)
        """, (nome, usuario, senha_hash, tipo))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def delete_usuario(id_usuario):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT tipo FROM usuarios WHERE id = ?", (id_usuario,))
    resultado = cursor.fetchone()
    if resultado and resultado[0] == 'admin':
        conn.close()
        return False, "Não é possível excluir o administrador principal"
    cursor.execute("DELETE FROM usuarios WHERE id = ?", (id_usuario,))
    conn.commit()
    conn.close()
    return True, "Usuário removido com sucesso"


def alterar_senha(id_usuario, nova_senha):
    conn = connect()
    cursor = conn.cursor()
    senha_hash = generate_password_hash(nova_senha)
    cursor.execute("""
        UPDATE usuarios SET senha = ? WHERE id = ?
    """, (senha_hash, id_usuario))
    conn.commit()
    conn.close()
    return True


def get_logs_acesso(limite=100):
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT usuario, acao, ip, data 
        FROM logs_acesso 
        ORDER BY id DESC 
        LIMIT ?
    """, (limite,))
    logs = cursor.fetchall()
    conn.close()
    return logs