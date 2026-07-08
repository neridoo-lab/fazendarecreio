# corrigir_banco.py
import sqlite3

conn = sqlite3.connect('rebanho.db')
cursor = conn.cursor()

# Lista de tabelas que podem ter a coluna 'sincronizado'
tabelas = ['animais', 'movimentacoes', 'usuarios', 'eventos']

for tabela in tabelas:
    try:
        cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN sincronizado INTEGER DEFAULT 0")
        print(f"✅ Coluna 'sincronizado' adicionada em {tabela}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print(f"ℹ️ Coluna já existe em {tabela}")
        elif "no such table" in str(e):
            print(f"⚠️ Tabela {tabela} não existe (ignorando)")
        else:
            print(f"❌ Erro em {tabela}: {e}")

conn.commit()
conn.close()

print("\n✅ Banco de dados corrigido! Agora tente fazer login novamente.")
input("Pressione Enter para sair...")