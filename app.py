from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, session
from database import (
    init_db, seed_categorias, seed_admin, connect,
    verificar_usuario, registrar_log, get_all_usuarios,
    add_usuario, delete_usuario, alterar_senha, get_logs_acesso,
    sincronizar_tudo, verificar_internet
)
from datetime import datetime
import csv
from io import StringIO, BytesIO
from functools import wraps
import sqlite3
import threading
import time

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("⚠️ ReportLab não instalado. pip install reportlab")

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta_aqui_mude_para_algo_seguro'

init_db()
seed_categorias()
seed_admin()


def sincronizador_automatico():
    while True:
        try:
            if verificar_internet():
                print("🌐 Internet detectada! Sincronizando...")
                sincronizar_tudo()
            else:
                print("📡 Sem internet. Modo offline.")
            time.sleep(60)
        except Exception as e:
            print(f"Erro no sincronizador: {e}")
            time.sleep(60)


thread_sincronizacao = threading.Thread(target=sincronizador_automatico, daemon=True)
thread_sincronizacao.start()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            flash('Por favor, faça login para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        if session.get('tipo') != 'admin':
            flash('Acesso negado. Área restrita ao administrador.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario")
        senha = request.form.get("senha")
        user = verificar_usuario(usuario, senha)
        if user:
            session['usuario_id'] = user['id']
            session['usuario'] = user['usuario']
            session['nome'] = user['nome']
            session['tipo'] = user['tipo']
            registrar_log(usuario, f"Login realizado", request.remote_addr)
            flash(f'Bem-vindo, {user["nome"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            registrar_log(usuario, f"Tentativa de login falhou", request.remote_addr)
            flash('Usuário ou senha inválidos!', 'danger')
    return render_template("login.html")


@app.route("/logout")
def logout():
    if 'usuario' in session:
        registrar_log(session['usuario'], f"Logout realizado", request.remote_addr)
    session.clear()
    flash('Você foi desconectado.', 'info')
    return redirect(url_for('login'))


@app.route("/")
@login_required
def dashboard():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT nome, quantidade FROM categorias ORDER BY id")
    categorias = cursor.fetchall()
    cursor.execute("""
        SELECT tipo, categoria, quantidade, data, usuario
        FROM movimentacoes
        ORDER BY id DESC
        LIMIT 10
    """)
    ultimas = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) FROM movimentacoes")
    total_movimentacoes = cursor.fetchone()[0]
    conn.close()
    total = sum([c[1] for c in categorias])
    total_femeas = 0
    total_machos = 0
    for c in categorias:
        if c[0] in ["Matrizes", "Novilhas", "Bezerras"]:
            total_femeas += c[1]
        elif c[0] in ["Touros", "Garrotes", "Bezerros"]:
            total_machos += c[1]
    online = verificar_internet()
    return render_template(
        "dashboard.html",
        categorias=categorias,
        ultimas=ultimas,
        total=total,
        total_femeas=total_femeas,
        total_machos=total_machos,
        total_movimentacoes=total_movimentacoes,
        usuario=session.get('nome'),
        tipo=session.get('tipo'),
        online=online
    )


@app.route("/sincronizar")
@login_required
def sincronizar_manual():
    if verificar_internet():
        enviados = sincronizar_tudo()
        flash(f"Sincronização concluída! {enviados} registros enviados.", "success")
    else:
        flash("Sem conexão com a internet. Tente novamente quando tiver sinal.", "warning")
    return redirect(url_for("dashboard"))


@app.route("/movimentacao", methods=["GET", "POST"])
@login_required
def movimentacao():
    conn = connect()
    cursor = conn.cursor()
    cursor.execute("SELECT nome FROM categorias ORDER BY id")
    categorias = [c[0] for c in cursor.fetchall()]
    
    if request.method == "POST":
        tipo = request.form["tipo"]
        quantidade = int(request.form["quantidade"])
        
        if quantidade <= 0:
            flash("A quantidade deve ser maior que zero!", "danger")
            conn.close()
            return render_template("movimentacao.html", categorias=categorias)
        
        # ========================================
        # TRANSFERÊNCIA ENTRE CATEGORIAS
        # ========================================
        if tipo == "Transferencia":
            categoria_origem = request.form.get("categoria_origem")
            categoria_destino = request.form.get("categoria_destino")
            sexo = request.form.get("sexo", "")
            
            if not categoria_origem or not categoria_destino:
                flash("Selecione a categoria de origem e destino!", "danger")
                conn.close()
                return render_template("movimentacao.html", categorias=categorias)
            
            if categoria_origem == categoria_destino:
                flash("A categoria de origem e destino não podem ser iguais!", "danger")
                conn.close()
                return render_template("movimentacao.html", categorias=categorias)
            
            # Verifica se tem estoque suficiente na origem
            cursor.execute("SELECT quantidade FROM categorias WHERE nome = ?", (categoria_origem,))
            resultado = cursor.fetchone()
            estoque_origem = resultado[0] if resultado else 0
            
            if quantidade > estoque_origem:
                flash(f"Estoque insuficiente em {categoria_origem}! Disponível: {estoque_origem}", "danger")
                conn.close()
                return render_template("movimentacao.html", categorias=categorias)
            
            # Registra a movimentação (origem - saída)
            cursor.execute("""
                INSERT INTO movimentacoes (tipo, categoria, sexo, quantidade, usuario, sincronizado)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (f"Transferência (saída)", categoria_origem, sexo, quantidade, session.get('usuario'), 0))
            
            # Registra a movimentação (destino - entrada)
            cursor.execute("""
                INSERT INTO movimentacoes (tipo, categoria, sexo, quantidade, usuario, sincronizado)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (f"Transferência (entrada)", categoria_destino, sexo, quantidade, session.get('usuario'), 0))
            
            # Atualiza a origem (diminui)
            cursor.execute("""
                UPDATE categorias
                SET quantidade = quantidade - ?
                WHERE nome = ?
            """, (quantidade, categoria_origem))
            
            # Atualiza o destino (aumenta)
            cursor.execute("""
                UPDATE categorias
                SET quantidade = quantidade + ?
                WHERE nome = ?
            """, (quantidade, categoria_destino))
            
            conn.commit()
            conn.close()
            
            registrar_log(session.get('usuario'), f"Transferência: {quantidade} de {categoria_origem} para {categoria_destino}", request.remote_addr)
            flash(f"✅ Transferência de {quantidade} {categoria_origem} → {categoria_destino} realizada com sucesso!", "success")
            
            if verificar_internet():
                sincronizar_tudo()
            
            return redirect(url_for("dashboard"))
        
        # ========================================
        # OUTROS TIPOS DE MOVIMENTAÇÃO
        # ========================================
        categoria = request.form["categoria"]
        sexo = request.form.get("sexo", "")
        
        # Verifica estoque para saídas
        if tipo in ["Venda", "Morte", "Abate"]:
            cursor.execute("SELECT quantidade FROM categorias WHERE nome = ?", (categoria,))
            resultado = cursor.fetchone()
            estoque_atual = resultado[0] if resultado else 0
            if quantidade > estoque_atual:
                flash(f"Estoque insuficiente! Disponível: {estoque_atual}", "danger")
                conn.close()
                return render_template("movimentacao.html", categorias=categorias)

        # Salva movimentação
        cursor.execute("""
            INSERT INTO movimentacoes (tipo, categoria, sexo, quantidade, usuario, sincronizado)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (tipo, categoria, sexo, quantidade, session.get('usuario'), 0))

        # Atualiza quantidade
        if tipo in ["Nascimento", "Compra"]:
            cursor.execute("""
                UPDATE categorias
                SET quantidade = quantidade + ?
                WHERE nome = ?
            """, (quantidade, categoria))
        else:
            cursor.execute("""
                UPDATE categorias
                SET quantidade = quantidade - ?
                WHERE nome = ?
            """, (quantidade, categoria))

        conn.commit()
        conn.close()
        
        registrar_log(session.get('usuario'), f"Movimentação: {tipo} - {categoria} x{quantidade}", request.remote_addr)
        flash(f"Movimentação registrada com sucesso!", "success")
        
        if verificar_internet():
            sincronizar_tudo()
        
        return redirect(url_for("dashboard"))
    
    conn.close()
    return render_template("movimentacao.html", categorias=categorias)


@app.route("/cadastro_inicial", methods=["GET", "POST"])
@admin_required
def cadastro_inicial():
    conn = connect()
    cursor = conn.cursor()
    if request.method == "POST":
        categorias = ["Matrizes", "Novilhas", "Bezerras", "Touros", "Garrotes", "Bezerros"]
        for categoria in categorias:
            quantidade = int(request.form[categoria])
            if quantidade < 0:
                flash(f"Quantidade negativa para {categoria}!", "danger")
                conn.close()
                return redirect(url_for("cadastro_inicial"))
            cursor.execute("""
                UPDATE categorias
                SET quantidade=?
                WHERE nome=?
            """, (quantidade, categoria))
        conn.commit()
        conn.close()
        registrar_log(session.get('usuario'), "Cadastro inicial atualizado", request.remote_addr)
        flash("Cadastro inicial atualizado com sucesso!", "success")
        return redirect(url_for("dashboard"))
    cursor.execute("SELECT nome, quantidade FROM categorias ORDER BY id")
    dados = cursor.fetchall()
    conn.close()
    return render_template("cadastro_inicial.html", dados=dados)


@app.route("/historico")
@login_required
def historico():
    conn = connect()
    cursor = conn.cursor()
    tipo = request.args.get('tipo', '')
    categoria = request.args.get('categoria', '')
    query = """
        SELECT tipo, categoria, sexo, quantidade, data, usuario
        FROM movimentacoes
        WHERE 1=1
    """
    params = []
    if tipo:
        query += " AND tipo = ?"
        params.append(tipo)
    if categoria:
        query += " AND categoria = ?"
        params.append(categoria)
    query += " ORDER BY id DESC"
    cursor.execute(query, params)
    movimentacoes = cursor.fetchall()
    cursor.execute("SELECT nome FROM categorias ORDER BY id")
    categorias = [c[0] for c in cursor.fetchall()]
    tipos = ["Nascimento", "Compra", "Venda", "Morte", "Abate", "Transferência (saída)", "Transferência (entrada)"]
    conn.close()
    return render_template(
        "historico.html",
        movimentacoes=movimentacoes,
        categorias=categorias,
        tipos=tipos,
        filtro_tipo=tipo,
        filtro_categoria=categoria
    )


@app.route("/relatorios")
@login_required
def relatorios():
    conn = connect()
    cursor = conn.cursor()
    
    # ========== FILTROS ==========
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    categoria_filtro = request.args.get('categoria', '')
    
    # ========== CONSTRUIR QUERY COM FILTROS ==========
    # Busca TODAS as movimentações primeiro
    query_movimentacoes = """
        SELECT tipo, categoria, sexo, quantidade, data, usuario
        FROM movimentacoes
        WHERE 1=1
    """
    params = []
    
    if data_inicio:
        query_movimentacoes += " AND DATE(data) >= ?"
        params.append(data_inicio)
    
    if data_fim:
        query_movimentacoes += " AND DATE(data) <= ?"
        params.append(data_fim)
    
    # NÃO FILTRA POR CATEGORIA AQUI - vamos fazer depois
    query_movimentacoes += " ORDER BY data DESC"
    
    cursor.execute(query_movimentacoes, params)
    movimentacoes_brutas = cursor.fetchall()
    
    # ========== PROCESSAR TRANSFERÊNCIAS ==========
    movimentacoes_processadas = []
    transferencias_dict = {}
    
    for m in movimentacoes_brutas:
        tipo = m[0]
        categoria = m[1]
        sexo = m[2]
        quantidade = m[3]
        data = m[4]
        usuario = m[5]
        
        # Verifica se é uma transferência
        if "Transferência" in tipo:
            # Extrai a data/hora para agrupar (remove os segundos para agrupar transferências do mesmo minuto)
            chave = f"{data[:16]}_{quantidade}_{usuario}"
            
            if chave not in transferencias_dict:
                transferencias_dict[chave] = {
                    'origem': None,
                    'destino': None,
                    'quantidade': quantidade,
                    'sexo': sexo,
                    'data': data,
                    'usuario': usuario,
                    'tipo': 'Transferência'
                }
            
            if "saída" in tipo:
                transferencias_dict[chave]['origem'] = categoria
            elif "entrada" in tipo:
                transferencias_dict[chave]['destino'] = categoria
        
        else:
            # Movimentações normais
            movimentacoes_processadas.append({
                'tipo': tipo,
                'categoria': categoria,
                'sexo': sexo,
                'quantidade': quantidade,
                'data': data,
                'usuario': usuario,
                'is_transferencia': False
            })
    
    # Adiciona as transferências agrupadas
    for chave, trans in transferencias_dict.items():
        if trans['origem'] and trans['destino']:
            # Verifica se a transferência deve ser mostrada com base no filtro de categoria
            mostrar = False
            
            if not categoria_filtro:
                # Se não tem filtro de categoria, mostra todas
                mostrar = True
            else:
                # Se tem filtro, mostra se a origem OU o destino forem a categoria filtrada
                if categoria_filtro == trans['origem'] or categoria_filtro == trans['destino']:
                    mostrar = True
            
            if mostrar:
                movimentacoes_processadas.append({
                    'tipo': '🔄 Progressão de Fase',
                    'categoria': f"{trans['origem']} → {trans['destino']}",
                    'sexo': trans['sexo'],
                    'quantidade': trans['quantidade'],
                    'data': trans['data'],
                    'usuario': trans['usuario'],
                    'is_transferencia': True,
                    'origem': trans['origem'],
                    'destino': trans['destino']
                })
    
    # Ordena por data (mais recente primeiro)
    movimentacoes_processadas.sort(key=lambda x: x['data'], reverse=True)
    
    # ========== TOTAIS POR CATEGORIA ==========
    cursor.execute("SELECT nome, quantidade FROM categorias ORDER BY id")
    categorias = cursor.fetchall()
    
    # ========== MOVIMENTAÇÕES POR TIPO (COM FILTROS) ==========
    query_tipo = """
        SELECT 
            CASE 
                WHEN tipo LIKE 'Transferência%' THEN '🔄 Progressão de Fase'
                ELSE tipo 
            END as tipo_agrupado,
            SUM(quantidade) as total
        FROM movimentacoes
        WHERE 1=1
    """
    params_tipo = []
    
    if data_inicio:
        query_tipo += " AND DATE(data) >= ?"
        params_tipo.append(data_inicio)
    
    if data_fim:
        query_tipo += " AND DATE(data) <= ?"
        params_tipo.append(data_fim)
    
    # Para o agrupamento por tipo, se tiver filtro de categoria, filtra por categoria
    if categoria_filtro:
        query_tipo += " AND categoria = ?"
        params_tipo.append(categoria_filtro)
    
    query_tipo += " GROUP BY tipo_agrupado ORDER BY tipo_agrupado"
    
    cursor.execute(query_tipo, params_tipo)
    movimentacoes_por_tipo = cursor.fetchall()
    
    # Corrige o nome das transferências no agrupamento
    movimentacoes_por_tipo_corrigido = []
    for item in movimentacoes_por_tipo:
        if 'Transferência' in item[0]:
            movimentacoes_por_tipo_corrigido.append(('🔄 Progressão de Fase', item[1]))
        else:
            movimentacoes_por_tipo_corrigido.append(item)
    
    # ========== TOTAL DE MOVIMENTAÇÕES ==========
    query_total = "SELECT COUNT(*) FROM movimentacoes WHERE 1=1"
    params_total = []
    
    if data_inicio:
        query_total += " AND DATE(data) >= ?"
        params_total.append(data_inicio)
    
    if data_fim:
        query_total += " AND DATE(data) <= ?"
        params_total.append(data_fim)
    
    if categoria_filtro:
        query_total += " AND categoria = ?"
        params_total.append(categoria_filtro)
    
    cursor.execute(query_total, params_total)
    total_movimentacoes = cursor.fetchone()[0]
    
    # ========== MOVIMENTAÇÕES POR SEXO ==========
    query_sexo = """
        SELECT sexo, SUM(quantidade) 
        FROM movimentacoes 
        WHERE sexo IS NOT NULL AND sexo != ''
    """
    params_sexo = []
    
    if data_inicio:
        query_sexo += " AND DATE(data) >= ?"
        params_sexo.append(data_inicio)
    
    if data_fim:
        query_sexo += " AND DATE(data) <= ?"
        params_sexo.append(data_fim)
    
    if categoria_filtro:
        query_sexo += " AND categoria = ?"
        params_sexo.append(categoria_filtro)
    
    query_sexo += " GROUP BY sexo"
    
    cursor.execute(query_sexo, params_sexo)
    movimentacoes_por_sexo = cursor.fetchall()
    
    # ========== LOGS DE ACESSO ==========
    cursor.execute("""
        SELECT usuario, acao, ip, data 
        FROM logs_acesso 
        ORDER BY id DESC 
        LIMIT 100
    """)
    logs_acesso = cursor.fetchall()
    
    # ========== BUSCAR CATEGORIAS PARA O FILTRO ==========
    cursor.execute("SELECT nome FROM categorias ORDER BY id")
    categorias_lista = [c[0] for c in cursor.fetchall()]
    
    conn.close()
    
    return render_template(
        "relatorios.html",
        categorias=categorias,
        movimentacoes_por_tipo=movimentacoes_por_tipo_corrigido,
        total_movimentacoes=total_movimentacoes,
        movimentacoes_por_sexo=movimentacoes_por_sexo,
        logs_acesso=logs_acesso,
        movimentacoes_filtradas=movimentacoes_processadas,
        categorias_lista=categorias_lista,
        data_inicio=data_inicio,
        data_fim=data_fim,
        categoria_filtro=categoria_filtro
    )


@app.route("/exportar/relatorio_pdf")
@login_required
def exportar_relatorio_pdf():
    """Exporta SOMENTE os dados filtrados em PDF"""
    if not REPORTLAB_AVAILABLE:
        flash("Biblioteca ReportLab não está instalada. Execute: pip install reportlab", "danger")
        return redirect(url_for("relatorios"))
    
    conn = connect()
    cursor = conn.cursor()
    
    # ========== PEGAR OS MESMOS FILTROS ==========
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    categoria_filtro = request.args.get('categoria', '')
    
    # ========== BUSCAR DADOS COM FILTROS ==========
    query = """
        SELECT tipo, categoria, sexo, quantidade, data, usuario
        FROM movimentacoes
        WHERE 1=1
    """
    params = []
    
    if data_inicio:
        query += " AND DATE(data) >= ?"
        params.append(data_inicio)
    
    if data_fim:
        query += " AND DATE(data) <= ?"
        params.append(data_fim)
    
    if categoria_filtro:
        query += " AND categoria = ?"
        params.append(categoria_filtro)
    
    query += " ORDER BY data DESC"
    
    cursor.execute(query, params)
    movimentacoes_brutas = cursor.fetchall()
    
    # ========== PROCESSAR TRANSFERÊNCIAS PARA O PDF ==========
    movimentacoes_processadas = []
    transferencias_dict = {}
    
    for m in movimentacoes_brutas:
        tipo = m[0]
        categoria = m[1]
        sexo = m[2]
        quantidade = m[3]
        data = m[4]
        usuario = m[5]
        
        if "Transferência" in tipo:
            chave = f"{data[:16]}_{quantidade}_{usuario}"
            
            if chave not in transferencias_dict:
                transferencias_dict[chave] = {
                    'origem': None,
                    'destino': None,
                    'quantidade': quantidade,
                    'sexo': sexo,
                    'data': data,
                    'usuario': usuario
                }
            
            if "saída" in tipo:
                transferencias_dict[chave]['origem'] = categoria
            elif "entrada" in tipo:
                transferencias_dict[chave]['destino'] = categoria
        
        else:
            movimentacoes_processadas.append({
                'tipo': tipo,
                'categoria': categoria,
                'sexo': sexo,
                'quantidade': quantidade,
                'data': data,
                'usuario': usuario,
                'is_transferencia': False
            })
    
    for chave, trans in transferencias_dict.items():
        if trans['origem'] and trans['destino']:
            movimentacoes_processadas.append({
                'tipo': 'Progressão de Fase',
                'categoria': f"{trans['origem']} → {trans['destino']}",
                'sexo': trans['sexo'],
                'quantidade': trans['quantidade'],
                'data': trans['data'],
                'usuario': trans['usuario'],
                'is_transferencia': True
            })
    
    movimentacoes_processadas.sort(key=lambda x: x['data'], reverse=True)
    
    conn.close()
    
    # ========== CRIAR PDF ==========
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    styles = getSampleStyleSheet()
    elementos = []
    
    # Título
    titulo_style = ParagraphStyle(
        'Titulo',
        parent=styles['Heading1'],
        fontSize=20,
        alignment=TA_CENTER,
        spaceAfter=20
    )
    elementos.append(Paragraph("Relatório do Rebanho - Fazenda Recreio", titulo_style))
    elementos.append(Spacer(1, 5))
    
    # Subtítulo com filtros
    subtitulo_style = ParagraphStyle(
        'Subtitulo',
        parent=styles['Normal'],
        fontSize=12,
        alignment=TA_CENTER,
        spaceAfter=20
    )
    
    filtros_texto = []
    if data_inicio:
        filtros_texto.append(f"Data Início: {data_inicio}")
    if data_fim:
        filtros_texto.append(f"Data Fim: {data_fim}")
    if categoria_filtro:
        filtros_texto.append(f"Categoria: {categoria_filtro}")
    
    texto_filtros = " | ".join(filtros_texto) if filtros_texto else "Sem filtros aplicados"
    elementos.append(Paragraph(f"Filtros: {texto_filtros}", subtitulo_style))
    elementos.append(Paragraph(f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}", subtitulo_style))
    elementos.append(Spacer(1, 15))
    
    # ========== LISTA DE MOVIMENTAÇÕES FILTRADAS ==========
    elementos.append(Paragraph(f"📋 Movimentações Filtradas ({len(movimentacoes_processadas)} registros)", 
                               ParagraphStyle('Resumo', parent=styles['Heading2'], fontSize=14, spaceAfter=10)))
    
    if movimentacoes_processadas:
        dados_mov = [['Tipo', 'Categoria', 'Sexo', 'Qtd', 'Data', 'Usuário']]
        for m in movimentacoes_processadas:
            sexo = m['sexo'] if m['sexo'] else '-'
            usuario = m['usuario'] if m['usuario'] else '-'
            dados_mov.append([m['tipo'], m['categoria'], sexo, str(m['quantidade']), m['data'], usuario])
        
        tabela_mov = Table(dados_mov, colWidths=[1.2*inch, 1.5*inch, 0.8*inch, 0.6*inch, 1.2*inch, 0.8*inch])
        tabela_mov.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.green),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
        ]))
        elementos.append(tabela_mov)
    else:
        elementos.append(Paragraph("Nenhuma movimentação encontrada com os filtros aplicados.", styles['Normal']))
    
    # ========== RODAPÉ ==========
    elementos.append(Spacer(1, 30))
    rodape_style = ParagraphStyle(
        'Rodape',
        parent=styles['Normal'],
        fontSize=8,
        alignment=TA_CENTER,
        textColor=colors.grey
    )
    elementos.append(Paragraph("Relatório gerado automaticamente pelo sistema Fazenda Recreio", rodape_style))
    
    doc.build(elementos)
    buffer.seek(0)
    
    registrar_log(session.get('usuario'), f"Exportou relatório PDF com filtros", request.remote_addr)
    
    return send_file(
        buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'relatorio_filtrado_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf'
    )


@app.route("/exportar/relatorio_csv")
@login_required
def exportar_relatorio_csv():
    """Exporta SOMENTE os dados filtrados em CSV"""
    conn = connect()
    cursor = conn.cursor()
    
    # ========== PEGAR OS MESMOS FILTROS ==========
    data_inicio = request.args.get('data_inicio', '')
    data_fim = request.args.get('data_fim', '')
    categoria_filtro = request.args.get('categoria', '')
    
    # ========== BUSCAR DADOS COM FILTROS ==========
    query = """
        SELECT tipo, categoria, sexo, quantidade, data, usuario
        FROM movimentacoes
        WHERE 1=1
    """
    params = []
    
    if data_inicio:
        query += " AND DATE(data) >= ?"
        params.append(data_inicio)
    
    if data_fim:
        query += " AND DATE(data) <= ?"
        params.append(data_fim)
    
    if categoria_filtro:
        query += " AND categoria = ?"
        params.append(categoria_filtro)
    
    query += " ORDER BY data DESC"
    
    cursor.execute(query, params)
    movimentacoes_brutas = cursor.fetchall()
    
    # ========== PROCESSAR TRANSFERÊNCIAS PARA O CSV ==========
    movimentacoes_processadas = []
    transferencias_dict = {}
    
    for m in movimentacoes_brutas:
        tipo = m[0]
        categoria = m[1]
        sexo = m[2]
        quantidade = m[3]
        data = m[4]
        usuario = m[5]
        
        if "Transferência" in tipo:
            chave = f"{data[:16]}_{quantidade}_{usuario}"
            
            if chave not in transferencias_dict:
                transferencias_dict[chave] = {
                    'origem': None,
                    'destino': None,
                    'quantidade': quantidade,
                    'sexo': sexo,
                    'data': data,
                    'usuario': usuario
                }
            
            if "saída" in tipo:
                transferencias_dict[chave]['origem'] = categoria
            elif "entrada" in tipo:
                transferencias_dict[chave]['destino'] = categoria
        
        else:
            movimentacoes_processadas.append({
                'tipo': tipo,
                'categoria': categoria,
                'sexo': sexo,
                'quantidade': quantidade,
                'data': data,
                'usuario': usuario
            })
    
    for chave, trans in transferencias_dict.items():
        if trans['origem'] and trans['destino']:
            movimentacoes_processadas.append({
                'tipo': 'Progressão de Fase',
                'categoria': f"{trans['origem']} → {trans['destino']}",
                'sexo': trans['sexo'],
                'quantidade': trans['quantidade'],
                'data': trans['data'],
                'usuario': trans['usuario']
            })
    
    movimentacoes_processadas.sort(key=lambda x: x['data'], reverse=True)
    
    conn.close()
    
    si = StringIO()
    writer = csv.writer(si, delimiter=',', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(['Tipo', 'Categoria', 'Sexo', 'Quantidade', 'Data', 'Usuário'])
    for m in movimentacoes_processadas:
        writer.writerow([m['tipo'], m['categoria'], m['sexo'] if m['sexo'] else '', m['quantidade'], m['data'], m['usuario'] if m['usuario'] else ''])
    output = si.getvalue()
    
    registrar_log(session.get('usuario'), f"Exportou relatório CSV com filtros", request.remote_addr)
    
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=relatorio_filtrado_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"}
    )


@app.route("/admin/usuarios")
@admin_required
def admin_usuarios():
    usuarios = get_all_usuarios()
    logs = get_logs_acesso(50)
    return render_template("admin_usuarios.html", usuarios=usuarios, logs=logs)


@app.route("/admin/usuario/add", methods=["POST"])
@admin_required
def admin_add_usuario():
    nome = request.form.get("nome")
    usuario = request.form.get("usuario")
    senha = request.form.get("senha")
    tipo = request.form.get("tipo", "funcionario")
    if not nome or not usuario or not senha:
        flash("Todos os campos são obrigatórios!", "danger")
        return redirect(url_for("admin_usuarios"))
    if len(senha) < 6:
        flash("A senha deve ter pelo menos 6 caracteres!", "danger")
        return redirect(url_for("admin_usuarios"))
    if add_usuario(nome, usuario, senha, tipo):
        registrar_log(session.get('usuario'), f"Adicionou usuário: {usuario}", request.remote_addr)
        flash(f"Usuário {usuario} adicionado com sucesso!", "success")
    else:
        flash(f"Erro: Usuário {usuario} já existe!", "danger")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuario/delete/<int:id>")
@admin_required
def admin_delete_usuario(id):
    sucesso, mensagem = delete_usuario(id)
    if sucesso:
        registrar_log(session.get('usuario'), f"Removeu usuário ID: {id}", request.remote_addr)
        flash(mensagem, "success")
    else:
        flash(mensagem, "danger")
    return redirect(url_for("admin_usuarios"))


@app.route("/admin/usuario/alterar_senha", methods=["POST"])
@admin_required
def admin_alterar_senha():
    id_usuario = request.form.get("id_usuario")
    nova_senha = request.form.get("nova_senha")
    if not id_usuario or not nova_senha:
        flash("Todos os campos são obrigatórios!", "danger")
        return redirect(url_for("admin_usuarios"))
    if len(nova_senha) < 6:
        flash("A senha deve ter pelo menos 6 caracteres!", "danger")
        return redirect(url_for("admin_usuarios"))
    alterar_senha(int(id_usuario), nova_senha)
    registrar_log(session.get('usuario'), f"Alterou senha do usuário ID: {id_usuario}", request.remote_addr)
    flash("Senha alterada com sucesso!", "success")
    return redirect(url_for("admin_usuarios"))


if __name__ == "__main__":
    import socket
    def get_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return '127.0.0.1'
    ip = get_ip()
    print("=" * 50)
    print("🏡 FAZENDA RECREIO - SISTEMA DE REBANHO")
    print("=" * 50)
    print(f"📍 Acesse no celular: http://{ip}:5000")
    print(f"📍 Acesse no computador: http://127.0.0.1:5000")
    print("=" * 50)
    print("👤 Usuário: admin")
    print("🔑 Senha: admin123")
    print("=" * 50)
    print("📡 Modo: Offline + Sincronização Automática")
    print("   - Funciona sem internet")
    print("   - Sincroniza automaticamente quando tem sinal")
    print("=" * 50)
    app.run(host='0.0.0.0', debug=True, port=5000)