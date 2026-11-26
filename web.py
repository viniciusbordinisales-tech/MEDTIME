"""
MEDTIME - Organização e cuidado com medicamentos (versão web, Etapa 1)

Como rodar:
    pip install -r requirements.txt
    python web.py
Depois abra no navegador:  http://127.0.0.1:5050

Estrutura:
    web.py        -> rotas, regras e banco de dados (SQLite)
    templates/    -> páginas HTML
    static/       -> CSS e JavaScript
    medtime.db    -> criado automaticamente na primeira execução
"""
import os
import sqlite3
from datetime import date, datetime, timedelta

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("MEDTIME_DB", os.path.join(BASE_DIR, "medtime.db"))

HOST = "127.0.0.1"
PORTA = 5050

app = Flask(__name__)
# Chave usada apenas para as mensagens na tela (flash). Em produção, trocar.
app.secret_key = os.environ.get("MEDTIME_SECRET", "medtime-chave-local-de-desenvolvimento")

DIAS_SEMANA = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira",
               "sexta-feira", "sábado", "domingo"]
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
ROTULOS_STATUS = {
    "pendente": "Pendente",
    "tomou": "Tomou",
    "nao_tomou": "Não tomou",
    "sem_registro": "Sem registro",
}

# --------------------------------------------------------------------------
# Banco de dados
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS medicamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    dosagem TEXT NOT NULL DEFAULT '',
    unidades_por_dose INTEGER NOT NULL DEFAULT 1,
    quantidade INTEGER NOT NULL DEFAULT 0,
    quantidade_minima INTEGER NOT NULL DEFAULT 5,
    horarios TEXT NOT NULL,
    data_inicio TEXT NOT NULL,
    duracao_dias INTEGER,
    observacoes TEXT NOT NULL DEFAULT '',
    exemplo INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    medicamento_id INTEGER NOT NULL REFERENCES medicamentos(id) ON DELETE CASCADE,
    data TEXT NOT NULL,
    horario TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('tomou', 'nao_tomou')),
    unidades_descontadas INTEGER NOT NULL DEFAULT 0,
    registrado_em TEXT NOT NULL,
    UNIQUE (medicamento_id, data, horario)
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def fechar_db(_erro):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def iniciar_banco():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------
# Funções de apoio (regras do sistema)
# --------------------------------------------------------------------------
def hoje():
    return date.today()


def lista_horarios(med):
    return [h for h in med["horarios"].split(",") if h]


def fim_tratamento(med):
    """Último dia do tratamento, ou None se o uso for contínuo."""
    if med["duracao_dias"]:
        inicio = date.fromisoformat(med["data_inicio"])
        return inicio + timedelta(days=med["duracao_dias"] - 1)
    return None


def ativo_no_dia(med, dia, para_historico=False):
    inicio = date.fromisoformat(med["data_inicio"])
    if para_historico:
        # No histórico, só cobramos registro a partir do dia do cadastro.
        inicio = max(inicio, date.fromisoformat(med["criado_em"]))
    fim = fim_tratamento(med)
    return dia >= inicio and (fim is None or dia <= fim)


def todos_medicamentos():
    linhas = get_db().execute(
        "SELECT * FROM medicamentos ORDER BY lower(nome)").fetchall()
    meds = []
    for linha in linhas:
        med = dict(linha)
        med["lista_horarios"] = lista_horarios(med)
        med["estoque"] = situacao_estoque(med)
        fim = fim_tratamento(med)
        med["fim"] = fim.isoformat() if fim else None
        meds.append(med)
    return meds


def situacao_estoque(med):
    """Calcula se o estoque está ok, baixo ou acabou."""
    consumo_diario = len(lista_horarios(med)) * med["unidades_por_dose"]
    dias = med["quantidade"] // consumo_diario if consumo_diario else None
    if med["quantidade"] <= 0:
        situacao = "acabou"
    elif med["quantidade"] <= med["quantidade_minima"] or (dias is not None and dias <= 3):
        situacao = "baixo"
    else:
        situacao = "ok"
    return {"situacao": situacao, "dias": dias}


def carregar_registros(desde, ate):
    linhas = get_db().execute(
        "SELECT * FROM doses WHERE data BETWEEN ? AND ?",
        (desde.isoformat(), ate.isoformat())).fetchall()
    return {(r["medicamento_id"], r["data"], r["horario"]): dict(r) for r in linhas}


def agenda_do_dia(meds, registros, dia, para_historico=False):
    """Lista as doses previstas para um dia, com o status de cada uma."""
    itens = []
    for med in meds:
        if not ativo_no_dia(med, dia, para_historico):
            continue
        for horario in med["lista_horarios"]:
            reg = registros.get((med["id"], dia.isoformat(), horario))
            if reg:
                status = reg["status"]
            else:
                status = "pendente" if dia >= hoje() else "sem_registro"
            itens.append({"med": med, "horario": horario, "status": status, "registro": reg})
    itens.sort(key=lambda i: (i["horario"], i["med"]["nome"].lower()))
    return itens


def ler_formulario(form):
    """Lê e valida o formulário de medicamento. Devolve (dados, erros)."""
    erros = []
    dados = {
        "nome": form.get("nome", "").strip(),
        "dosagem": form.get("dosagem", "").strip(),
        "observacoes": form.get("observacoes", "").strip(),
        "data_inicio": form.get("data_inicio", "").strip(),
    }

    def inteiro(campo, rotulo, minimo, obrigatorio=True):
        texto = form.get(campo, "").strip()
        if not texto:
            if obrigatorio:
                erros.append(f"Preencha {rotulo}.")
            return None
        try:
            valor = int(texto)
        except ValueError:
            erros.append(f"{rotulo[0].upper() + rotulo[1:]} deve ser um número inteiro.")
            return None
        if valor < minimo:
            erros.append(f"{rotulo[0].upper() + rotulo[1:]} deve ser pelo menos {minimo}.")
            return None
        return valor

    if not dados["nome"]:
        erros.append("Preencha o nome do medicamento.")

    dados["unidades_por_dose"] = inteiro("unidades_por_dose", "as unidades por dose", 1)
    dados["quantidade"] = inteiro("quantidade", "a quantidade disponível", 0)
    dados["quantidade_minima"] = inteiro("quantidade_minima", "o aviso de estoque baixo", 0)
    dados["duracao_dias"] = inteiro("duracao_dias", "a duração do tratamento", 1, obrigatorio=False)

    horarios = set()
    for bruto in form.getlist("horario"):
        bruto = bruto.strip()
        if not bruto:
            continue
        try:
            horarios.add(datetime.strptime(bruto, "%H:%M").strftime("%H:%M"))
        except ValueError:
            erros.append(f"Horário inválido: {bruto}.")
    if not horarios:
        erros.append("Informe pelo menos um horário.")
    dados["horarios"] = ",".join(sorted(horarios))

    try:
        date.fromisoformat(dados["data_inicio"])
    except ValueError:
        erros.append("Informe a data de início do tratamento.")

    return dados, erros


# --------------------------------------------------------------------------
# Filtros e dados globais dos templates
# --------------------------------------------------------------------------
@app.template_filter("data_br")
def data_br(iso):
    return date.fromisoformat(iso).strftime("%d/%m/%Y") if iso else ""


@app.template_filter("dia_curto")
def dia_curto(dia):
    return f"{DIAS_SEMANA[dia.weekday()].split('-')[0]}, {dia.day:02d}/{dia.month:02d}"


@app.context_processor
def dados_globais():
    return {
        "menu": [("painel", "Hoje"), ("medicamentos", "Remédios"), ("estoque", "Estoque"),
                 ("consulta", "Consulta"), ("historico", "Histórico")],
        "rotulos_status": ROTULOS_STATUS,
    }


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------
@app.route("/")
def painel():
    meds = todos_medicamentos()
    dia = hoje()
    registros = carregar_registros(dia, dia)
    doses = agenda_do_dia(meds, registros, dia)
    alertas = [m for m in meds if m["estoque"]["situacao"] in ("baixo", "acabou")]
    resumo = {
        "total": len(doses),
        "tomou": sum(1 for d in doses if d["status"] == "tomou"),
        "pendente": sum(1 for d in doses if d["status"] == "pendente"),
    }
    data_extenso = f"{DIAS_SEMANA[dia.weekday()].capitalize()}, {dia.day} de {MESES[dia.month - 1]}"
    return render_template("painel.html", doses=doses, alertas=alertas, resumo=resumo,
                           data_extenso=data_extenso, tem_medicamentos=bool(meds))


@app.post("/dose")
def registrar_dose():
    db = get_db()
    try:
        med_id = int(request.form["medicamento_id"])
    except (KeyError, ValueError):
        abort(400)
    horario = request.form.get("horario", "")
    status = request.form.get("status", "")
    if status not in ("tomou", "nao_tomou", "pendente"):
        abort(400)

    med = db.execute("SELECT * FROM medicamentos WHERE id = ?", (med_id,)).fetchone()
    if med is None or horario not in lista_horarios(med):
        abort(404)

    dia = hoje().isoformat()
    anterior = db.execute(
        "SELECT * FROM doses WHERE medicamento_id = ? AND data = ? AND horario = ?",
        (med_id, dia, horario)).fetchone()

    # Se já havia registro, devolve ao estoque o que foi descontado antes.
    if anterior:
        db.execute("UPDATE medicamentos SET quantidade = quantidade + ? WHERE id = ?",
                   (anterior["unidades_descontadas"], med_id))
        db.execute("DELETE FROM doses WHERE id = ?", (anterior["id"],))

    if status in ("tomou", "nao_tomou"):
        atual = db.execute("SELECT quantidade FROM medicamentos WHERE id = ?",
                           (med_id,)).fetchone()["quantidade"]
        descontar = min(med["unidades_por_dose"], atual) if status == "tomou" else 0
        db.execute("UPDATE medicamentos SET quantidade = quantidade - ? WHERE id = ?",
                   (descontar, med_id))
        db.execute(
            "INSERT INTO doses (medicamento_id, data, horario, status, unidades_descontadas, registrado_em)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (med_id, dia, horario, status, descontar, datetime.now().isoformat(timespec="seconds")))
    db.commit()
    return redirect(request.referrer or url_for("painel"))


@app.route("/medicamentos")
def medicamentos():
    return render_template("medicamentos.html", meds=todos_medicamentos())


def _formulario(med=None):
    """Mostra e processa o formulário de cadastro/edição."""
    db = get_db()
    if request.method == "POST":
        dados, erros = ler_formulario(request.form)
        if not erros:
            if med is None:
                db.execute(
                    "INSERT INTO medicamentos (nome, dosagem, unidades_por_dose, quantidade,"
                    " quantidade_minima, horarios, data_inicio, duracao_dias, observacoes, exemplo, criado_em)"
                    " VALUES (:nome, :dosagem, :unidades_por_dose, :quantidade, :quantidade_minima,"
                    " :horarios, :data_inicio, :duracao_dias, :observacoes, 0, :criado_em)",
                    {**dados, "criado_em": hoje().isoformat()})
                flash("Medicamento cadastrado.", "ok")
            else:
                db.execute(
                    "UPDATE medicamentos SET nome=:nome, dosagem=:dosagem, unidades_por_dose=:unidades_por_dose,"
                    " quantidade=:quantidade, quantidade_minima=:quantidade_minima, horarios=:horarios,"
                    " data_inicio=:data_inicio, duracao_dias=:duracao_dias, observacoes=:observacoes"
                    " WHERE id=:id", {**dados, "id": med["id"]})
                flash("Alterações salvas.", "ok")
            db.commit()
            return redirect(url_for("medicamentos"))
        valores = {**dados, "lista_horarios": dados["horarios"].split(",") if dados["horarios"] else []}
        valores["exemplo"] = med["exemplo"] if med else 0
        return render_template("medicamento_form.html", med=valores, erros=erros,
                               editando=med is not None)

    if med is None:
        valores = {"nome": "", "dosagem": "", "unidades_por_dose": 1, "quantidade": "",
                   "quantidade_minima": 5, "lista_horarios": ["08:00"],
                   "data_inicio": hoje().isoformat(), "duracao_dias": "", "observacoes": "",
                   "exemplo": 0}
    else:
        valores = dict(med)
        valores["lista_horarios"] = lista_horarios(med)
        valores["duracao_dias"] = med["duracao_dias"] or ""
    return render_template("medicamento_form.html", med=valores, erros=[], editando=med is not None)


@app.route("/medicamentos/novo", methods=["GET", "POST"])
def novo_medicamento():
    return _formulario()


@app.route("/medicamentos/<int:med_id>/editar", methods=["GET", "POST"])
def editar_medicamento(med_id):
    med = get_db().execute("SELECT * FROM medicamentos WHERE id = ?", (med_id,)).fetchone()
    if med is None:
        abort(404)
    return _formulario(dict(med))


@app.post("/medicamentos/<int:med_id>/excluir")
def excluir_medicamento(med_id):
    db = get_db()
    db.execute("DELETE FROM medicamentos WHERE id = ?", (med_id,))
    db.commit()
    flash("Medicamento excluído, junto com o histórico de doses dele.", "ok")
    return redirect(url_for("medicamentos"))


@app.route("/estoque")
def estoque():
    meds = todos_medicamentos()
    ordem = {"acabou": 0, "baixo": 1, "ok": 2}
    meds.sort(key=lambda m: (ordem[m["estoque"]["situacao"]], m["nome"].lower()))
    return render_template("estoque.html", meds=meds)


@app.post("/estoque/<int:med_id>/repor")
def repor_estoque(med_id):
    try:
        quantidade = int(request.form.get("adicionar", ""))
    except ValueError:
        quantidade = 0
    if quantidade < 1:
        flash("Informe quantas unidades você recebeu (1 ou mais).", "erro")
        return redirect(url_for("estoque"))
    db = get_db()
    db.execute("UPDATE medicamentos SET quantidade = quantidade + ? WHERE id = ?",
               (quantidade, med_id))
    db.commit()
    flash("Estoque atualizado.", "ok")
    return redirect(url_for("estoque"))


@app.route("/consulta")
def consulta():
    return render_template("consulta.html")


@app.route("/historico")
def historico():
    try:
        dias = int(request.args.get("dias", 7))
    except ValueError:
        dias = 7
    dias = dias if dias in (7, 30, 90) else 7

    meds = todos_medicamentos()
    fim = hoje()
    inicio = fim - timedelta(days=dias - 1)
    registros = carregar_registros(inicio, fim)

    grupos = []
    total = {"tomou": 0, "nao_tomou": 0, "sem_registro": 0}
    for i in range(dias):
        dia = fim - timedelta(days=i)
        itens = agenda_do_dia(meds, registros, dia, para_historico=True)
        if not itens:
            continue
        for item in itens:
            if item["status"] in total:
                total[item["status"]] += 1
        grupos.append({"dia": dia, "itens": itens})
    return render_template("historico.html", grupos=grupos, total=total, dias=dias)


# --------------------------------------------------------------------------
# Dados de exemplo (FICTÍCIOS) - sempre marcados na tela
# --------------------------------------------------------------------------
EXEMPLOS = [
    # nome, dosagem, unidades, quantidade final, mínimo, horários, duração
    ("Remédio de exemplo A", "1 comprimido (exemplo)", 1, 26, 5, "08:00,20:00", None),
    ("Remédio de exemplo B", "1 cápsula (exemplo)", 1, 4, 5, "12:00", None),
    ("Remédio de exemplo C", "1 comprimido (exemplo)", 1, 22, 5, "06:00,14:00,22:00", 10),
]


@app.post("/exemplo/carregar")
def carregar_exemplo():
    db = get_db()
    ja_existe = db.execute("SELECT COUNT(*) FROM medicamentos WHERE exemplo = 1").fetchone()[0]
    if ja_existe:
        flash("Os dados de exemplo já estão carregados.", "ok")
        return redirect(url_for("painel"))

    inicio = hoje() - timedelta(days=6)
    for idx, (nome, dosagem, unid, qtd, minimo, horarios, duracao) in enumerate(EXEMPLOS):
        cur = db.execute(
            "INSERT INTO medicamentos (nome, dosagem, unidades_por_dose, quantidade, quantidade_minima,"
            " horarios, data_inicio, duracao_dias, observacoes, exemplo, criado_em)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
            (nome, dosagem, unid, qtd, minimo, horarios, inicio.isoformat(), duracao,
             "Dado fictício, criado só para demonstrar o sistema.", inicio.isoformat()))
        med_id = cur.lastrowid
        # Histórico fictício dos últimos 6 dias (hoje fica em aberto).
        for dias_atras in range(6, 0, -1):
            dia = hoje() - timedelta(days=dias_atras)
            for pos, horario in enumerate(horarios.split(",")):
                if (dias_atras + idx + pos) % 7 == 3:
                    continue  # dose sem registro
                status = "nao_tomou" if (dias_atras + idx + pos) % 5 == 0 else "tomou"
                db.execute(
                    "INSERT INTO doses (medicamento_id, data, horario, status, unidades_descontadas, registrado_em)"
                    " VALUES (?, ?, ?, ?, 0, ?)",
                    (med_id, dia.isoformat(), horario, status, f"{dia.isoformat()}T{horario}:00"))
    db.commit()
    flash("Dados de exemplo (fictícios) carregados. Você pode removê-los a qualquer momento.", "ok")
    return redirect(url_for("painel"))


@app.post("/exemplo/remover")
def remover_exemplo():
    db = get_db()
    db.execute("DELETE FROM medicamentos WHERE exemplo = 1")
    db.commit()
    flash("Dados de exemplo removidos.", "ok")
    return redirect(url_for("medicamentos"))


iniciar_banco()

if __name__ == "__main__":
    app.run(host=HOST, port=PORTA, debug=True)
