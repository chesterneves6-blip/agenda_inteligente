import streamlit as st
import pandas as pd
import googlemaps
from io import BytesIO
import sqlite3
import bcrypt
import datetime
import os

# --- Configurações ---
GOOGLE_API_KEY = st.secrets.get("AIzaSyAGUxmu5J5rYwLCs2iOYXiyS3Znms9qk_Y", "")  # Usar secrets do Streamlit Cloud

CONSUMO_KM_L = 9
PRECO_COMBUSTIVEL = 4.49
CAPACIDADE_TANQUE = 54

# --- Banco SQLite ---
conn = sqlite3.connect("usuarios.db", check_same_thread=False)
c = conn.cursor()

# Criação das tabelas (adicionando endereco_partida)
c.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    senha_hash BLOB NOT NULL,
    endereco_partida TEXT NOT NULL
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS atendimentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_email TEXT NOT NULL,
    cliente TEXT NOT NULL,
    endereco TEXT NOT NULL,
    data TEXT NOT NULL,
    inicio TEXT NOT NULL,
    fim TEXT NOT NULL,
    atividade TEXT NOT NULL,
    observacoes TEXT
)
""")
conn.commit()

# --- Funções usuário ---
def hash_password(password: str) -> bytes:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt())

def check_password(password: str, hashed: bytes) -> bool:
    return bcrypt.checkpw(password.encode(), hashed)

def criar_usuario(nome, email, senha, endereco_partida):
    senha_hash = hash_password(senha)
    try:
        c.execute("INSERT INTO usuarios (nome, email, senha_hash, endereco_partida) VALUES (?, ?, ?, ?)",
                  (nome, email, senha_hash, endereco_partida))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False

def autenticar_usuario(email, senha):
    c.execute("SELECT nome, senha_hash, endereco_partida FROM usuarios WHERE email = ?", (email,))
    res = c.fetchone()
    if res:
        nome, senha_hash, endereco_partida = res
        if check_password(senha, senha_hash):
            return nome, endereco_partida
    return None, None

def atualizar_endereco_partida(email, novo_endereco):
    c.execute("UPDATE usuarios SET endereco_partida = ? WHERE email = ?", (novo_endereco, email))
    conn.commit()

# --- Funções atendimentos ---
def salvar_atendimento(usuario_email, atendimento):
    c.execute("""
        INSERT INTO atendimentos (usuario_email, cliente, endereco, data, inicio, fim, atividade, observacoes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        usuario_email,
        atendimento["Cliente"],
        atendimento["Endereco"],
        atendimento["Data"],
        atendimento["Inicio"],
        atendimento["Fim"],
        atendimento["Atividade"],
        atendimento["Observacoes"]
    ))
    conn.commit()

def buscar_atendimentos(usuario_email):
    c.execute(
        "SELECT cliente, endereco, data, inicio, fim, atividade, observacoes "
        "FROM atendimentos WHERE usuario_email = ? ORDER BY data, inicio",
        (usuario_email,)
    )
    rows = c.fetchall()
    colunas = ["Cliente", "Endereco", "Data", "Inicio", "Fim", "Atividade", "Observacoes"]
    df = pd.DataFrame(rows, columns=colunas)
    return df

# --- Funções cálculo ---
def calcular_rota(origem, destino, gmaps_client):
    try:
        rota = gmaps_client.distance_matrix(
            origins=origem,
            destinations=destino,
            mode="driving",
            departure_time="now"
        )
        elemento = rota["rows"][0]["elements"][0]
        if elemento["status"] != "OK":
            return None
        tempo_segundos = elemento.get("duration_in_traffic", elemento.get("duration"))["value"]
        tempo_min = max(1, round(tempo_segundos / 60))
        distancia_km = round(elemento["distance"]["value"] / 1000, 1)
        return {"tempo": tempo_min, "distancia": distancia_km}
    except Exception as e:
        st.error(f"Erro ao calcular rota: {e}")
        return None

def processar_atendimentos(atendimentos, endereco_partida, gmaps_client):
    if atendimentos.empty:
        st.warning("Nenhum atendimento cadastrado!")
        return None, None

    agenda = atendimentos.rename(columns={"Cliente": "Aluno", "Data": "Dia"})

    rotas = []
    km_total = 0
    tempo_total = 0
    dias = agenda["Dia"].unique()

    for dia in dias:
        agenda_dia = agenda[agenda["Dia"] == dia].sort_values("Inicio").reset_index(drop=True)
        primeiro_cliente = agenda_dia.iloc[0]
        rota = calcular_rota(endereco_partida, primeiro_cliente["Endereco"], gmaps_client)
        if rota:
            km_total += rota["distancia"]
            tempo_total += rota["tempo"]
            rotas.append({
                "Dia": dia,
                "Origem": "Casa",
                "Destino": primeiro_cliente["Aluno"],
                "Distancia_KM": rota["distancia"],
                "Tempo_Min": rota["tempo"],
                "Status": "IDA"
            })

        for i in range(len(agenda_dia) - 1):
            atual = agenda_dia.iloc[i]
            proximo = agenda_dia.iloc[i + 1]
            rota = calcular_rota(atual["Endereco"], proximo["Endereco"], gmaps_client)
            if rota is None:
                continue
            tempo = rota["tempo"]
            distancia = rota["distancia"]
            fim_atual = pd.to_datetime(atual["Fim"], format="%H:%M")
            inicio_proxima = pd.to_datetime(proximo["Inicio"], format="%H:%M")
            chegada = fim_atual + pd.Timedelta(minutes=tempo)
            folga = int((inicio_proxima - chegada).total_seconds() / 60)
            if folga >= 10:
                status = "OK"
            elif folga >= 0:
                status = "ATENCAO"
            else:
                status = "ATRASO"
            km_total += distancia
            tempo_total += tempo
            rotas.append({
                "Dia": dia,
                "Origem": atual["Aluno"],
                "Destino": proximo["Aluno"],
                "Distancia_KM": distancia,
                "Tempo_Min": tempo,
                "Chegada_Prevista": chegada.strftime("%H:%M"),
                "Folga_Min": folga,
                "Status": status
            })

        ultimo = agenda_dia.iloc[-1]
        rota = calcular_rota(ultimo["Endereco"], endereco_partida, gmaps_client)
        if rota:
            km_total += rota["distancia"]
            tempo_total += rota["tempo"]
            rotas.append({
                "Dia": dia,
                "Origem": ultimo["Aluno"],
                "Destino": "Casa",
                "Distancia_KM": rota["distancia"],
                "Tempo_Min": rota["tempo"],
                "Status": "VOLTA"
            })

    resultado_rotas = pd.DataFrame(rotas)
    total_atendimentos = len(agenda)
    total_clientes = agenda["Aluno"].nunique()
    total_rotas = len(resultado_rotas)
    km_mes = round(km_total * 4, 1)
    litros_semana = round(km_total / CONSUMO_KM_L, 2)
    litros_mes = round(litros_semana * 4, 2)
    gasto_semana = round(litros_semana * PRECO_COMBUSTIVEL, 2)
    gasto_mes = round(gasto_semana * 4, 2)
    tanques_mes = round(litros_mes / CAPACIDADE_TANQUE, 2)
    custo_por_atendimento = round(gasto_semana / total_atendimentos, 2)
    custo_por_cliente = round(gasto_semana / total_clientes, 2)

    dashboard = pd.DataFrame({
        "Indicador": [
            "Total de Atendimentos",
            "Total de Clientes",
            "Total de Rotas",
            "Km Semana",
            "Km Mes",
            "Tempo Trânsito Min",
            "Litros Semana",
            "Litros Mes",
            "Preço Combustível",
            "Capacidade Tanque",
            "Gasto Semana",
            "Gasto Mes",
            "Tanques Mes",
            "Custo por Atendimento",
            "Custo por Cliente"
        ],
        "Valor": [
            total_atendimentos,
            total_clientes,
            total_rotas,
            km_total,
            km_mes,
            tempo_total,
            litros_semana,
            litros_mes,
            PRECO_COMBUSTIVEL,
            CAPACIDADE_TANQUE,
            gasto_semana,
            gasto_mes,
            tanques_mes,
            custo_por_atendimento,
            custo_por_cliente
        ]
    })
    return resultado_rotas, dashboard

def gera_excel(agenda, resultado_rotas, dashboard):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        agenda.to_excel(writer, sheet_name="Agenda", index=False)
        resultado_rotas.to_excel(writer, sheet_name="Resultado_Rotas", index=False)
        dashboard.to_excel(writer, sheet_name="Dashboard_Financeiro", index=False)
    return output.getvalue()

# --- Telas ---
def pagina_login():
    st.title("Login")

    email = st.text_input("Email")
    senha = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        nome, endereco_partida = autenticar_usuario(email, senha)
        if nome:
            st.session_state["logado"] = True
            st.session_state["nome"] = nome
            st.session_state["email"] = email
            st.session_state["endereco_partida"] = endereco_partida
            st.experimental_rerun()
        else:
            st.error("Email ou senha incorretos")

def pagina_cadastro():
    st.title("Cadastro de Usuário")

    nome = st.text_input("Nome completo")
    email = st.text_input("Email")
    senha = st.text_input("Senha", type="password")
    senha2 = st.text_input("Confirme a senha", type="password")
    endereco_partida = st.text_input("Endereço de Partida (ex: sua casa)")

    if st.button("Cadastrar"):
        if not nome or not email or not senha or not senha2 or not endereco_partida:
            st.error("Por favor, preencha todos os campos")
        elif senha != senha2:
            st.error("As senhas não conferem")
        else:
            sucesso = criar_usuario(nome, email, senha, endereco_partida)
            if sucesso:
                st.success("Usuário criado com sucesso! Faça login.")
                st.session_state["pagina"] = "login"
            else:
                st.error("Email já cadastrado")

def pagina_cadastro_atendimentos():
    st.header("Cadastro de Atendimentos")

    usuario_email = st.session_state.get("email", "")

    with st.form("form_atendimento", clear_on_submit=True):
        cliente = st.text_input("Cliente")
        endereco = st.text_input("Endereço")
        data_atendimento = st.date_input("Data do Atendimento", datetime.date.today())
        horario_inicio = st.time_input("Horário de Início")
        horario_fim = st.time_input("Horário de Fim")
        atividade = st.selectbox("Atividade", ["Ativação", "Alteração", "Desativação"])
        observacoes = st.text_area("Observações")
        submit = st.form_submit_button("Adicionar Atendimento")

    if submit:
        if not cliente or not endereco:
            st.error("Cliente e endereço são obrigatórios.")
        else:
            atendimento = {
                "Cliente": cliente,
                "Endereco": endereco,
                "Data": data_atendimento.strftime("%Y-%m-%d"),
                "Inicio": horario_inicio.strftime("%H:%M"),
                "Fim": horario_fim.strftime("%H:%M"),
                "Atividade": atividade,
                "Observacoes": observacoes
            }
            salvar_atendimento(usuario_email, atendimento)
            st.success(f"Atendimento do cliente {cliente} salvo no banco de dados!")

    df = buscar_atendimentos(usuario_email)
    if not df.empty:
        st.subheader("Atendimentos cadastrados")
        st.dataframe(df)

def app_principal():
    st.sidebar.write(f"Olá, {st.session_state['nome']}")
    st.sidebar.write(f"Endereço de partida: {st.session_state.get('endereco_partida','')}")
    if st.sidebar.button("Logout"):
        st.session_state["logado"] = False
        st.experimental_rerun()

    pagina_cadastro_atendimentos()

    if st.button("Processar Rotas e Custos"):
        if not GOOGLE_API_KEY:
            st.error("Chave GOOGLE_API_KEY não configurada nos secrets! Veja a documentação.")
            return

        gmaps_client = googlemaps.Client(key=GOOGLE_API_KEY)

        usuario_email = st.session_state.get("email", "")
        endereco_partida = st.session_state.get("endereco_partida", ENDERECO_CASA)
        df_agenda = buscar_atendimentos(usuario_email)
        if df_agenda.empty:
            st.warning("Você não tem atendimentos cadastrados para processar.")
            return

        resultado_rotas, dashboard = processar_atendimentos(df_agenda, endereco_partida, gmaps_client)
        if resultado_rotas is not None and dashboard is not None:
            st.header("Dashboard Financeiro")
            st.dataframe(dashboard)

            st.header("Rotas Calculadas")
            st.dataframe(resultado_rotas)

            excel_data = gera_excel(df_agenda, resultado_rotas, dashboard)
            st.download_button(
                label="Download do Excel com resultados",
                data=excel_data,
                file_name="Resultado_Agenda_Inteligente.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

def main():
    if "logado" not in st.session_state:
        st.session_state["logado"] = False
        st.session_state["nome"] = ""
        st.session_state["email"] = ""
        st.session_state["endereco_partida"] = ""
        st.session_state["pagina"] = "login"

    menu = ["Login", "Cadastro"]
    if st.session_state["logado"]:
        app_principal()
    else:
        st.sidebar.title("Menu")
        opcao = st.sidebar.radio("Navegação", menu)
        if opcao == "Login":
            pagina_login()
        else:
            pagina_cadastro()

if __name__ == "__main__":
    main()
