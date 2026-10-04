"""
Buscador de Publicações e Intimações - OAB
==========================================

Consulta o DJEN (Diário de Justiça Eletrônico Nacional / Comunica PJe - CNJ)
pela API pública e lista as publicações/intimações vinculadas a uma OAB.

Uso:
    python buscador_publicacoes.py            -> abre a janela (interface gráfica)
    python buscador_publicacoes.py --cli      -> busca no terminal (últimos 7 dias)
    python buscador_publicacoes.py --cli --inicio 2026-09-01 --fim 2026-09-30

Usa apenas a biblioteca padrão do Python (sem dependências externas).
"""

import argparse
import csv
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta

APP_NOME = "Buscador de Publicações OAB"
API_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
CONSULTA_WEB = "https://comunica.pje.jus.br/consulta"
ITENS_POR_PAGINA = 100

OAB_PADRAO = "517745"
UF_PADRAO = "SP"

UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
    "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
    "SE", "SP", "TO",
]


# --------------------------------------------------------------------------
# Configuração e histórico local (pasta do usuário)
# --------------------------------------------------------------------------

def pasta_dados():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    pasta = os.path.join(base, "BuscadorPublicacoesOAB")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def _carregar_json(nome, padrao):
    try:
        with open(os.path.join(pasta_dados(), nome), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def _salvar_json(nome, dados):
    try:
        with open(os.path.join(pasta_dados(), nome), "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def carregar_config():
    cfg = {"oab": OAB_PADRAO, "uf": UF_PADRAO, "dias": 7}
    cfg.update(_carregar_json("config.json", {}))
    return cfg


def salvar_config(cfg):
    _salvar_json("config.json", cfg)


def carregar_lidas():
    return set(_carregar_json("lidas.json", []))


def salvar_lidas(lidas):
    _salvar_json("lidas.json", sorted(lidas))


# --------------------------------------------------------------------------
# Consulta à API do DJEN
# --------------------------------------------------------------------------

class ErroConsulta(Exception):
    pass


def _get_json(params, tentativas=4):
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (BuscadorPublicacoesOAB)",
    })
    espera = 2
    for tentativa in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # 429 = muitas requisições; 5xx = instabilidade do servidor
            if e.code in (429, 500, 502, 503, 504) and tentativa < tentativas - 1:
                time.sleep(espera)
                espera *= 2
                continue
            raise ErroConsulta(f"O servidor do DJEN respondeu com erro HTTP {e.code}.") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if tentativa < tentativas - 1:
                time.sleep(espera)
                espera *= 2
                continue
            raise ErroConsulta(
                "Não foi possível conectar ao DJEN. Verifique sua internet "
                f"e tente novamente.\n\nDetalhe: {e}") from e
        except ValueError as e:
            raise ErroConsulta("Resposta inválida do servidor do DJEN.") from e
    raise ErroConsulta("Falha ao consultar o DJEN.")


def _limpar_texto(texto):
    if not texto:
        return ""
    texto = re.sub(r"<br\s*/?>|</p>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = html.unescape(texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n\s*\n\s*\n+", "\n\n", texto)
    return texto.strip()


def _primeiro(item, *chaves, padrao=""):
    for chave in chaves:
        valor = item.get(chave)
        if valor not in (None, ""):
            return valor
    return padrao


def _formatar_data(valor):
    if not valor:
        return ""
    valor = str(valor)[:10]
    try:
        return datetime.strptime(valor, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valor


def normalizar(item):
    """Converte um item da API num dicionário simples e estável."""
    partes = [
        f"{d.get('nome', '')} ({d.get('polo', '')})".replace(" ()", "")
        for d in item.get("destinatarios") or []
    ]
    advogados = []
    for d in item.get("destinatarioadvogados") or []:
        adv = d.get("advogado") or {}
        if adv:
            advogados.append(f"{adv.get('nome', '')} - OAB {adv.get('numero_oab', '')}/{adv.get('uf_oab', '')}")
    return {
        "id": str(_primeiro(item, "id", "hash")),
        "data": _formatar_data(_primeiro(item, "data_disponibilizacao", "datadisponibilizacao")),
        "tribunal": _primeiro(item, "siglaTribunal"),
        "processo": _primeiro(item, "numeroprocessocommascara", "numero_processo"),
        "tipo": _primeiro(item, "tipoComunicacao"),
        "documento": _primeiro(item, "tipoDocumento"),
        "orgao": _primeiro(item, "nomeOrgao"),
        "classe": _primeiro(item, "nomeClasse"),
        "partes": "; ".join(partes),
        "advogados": "; ".join(advogados),
        "link": _primeiro(item, "link"),
        "texto": _limpar_texto(_primeiro(item, "texto")),
    }


def buscar_publicacoes(oab, uf, inicio, fim, progresso=None):
    """Busca todas as publicações da OAB no período (datas no formato date)."""
    oab = re.sub(r"\D", "", str(oab))
    if not oab:
        raise ErroConsulta("Informe o número da OAB.")
    if inicio > fim:
        raise ErroConsulta("A data inicial é posterior à data final.")

    resultados, vistos = [], set()
    pagina, total = 1, None
    while True:
        dados = _get_json({
            "numeroOab": oab,
            "ufOab": uf.upper(),
            "dataDisponibilizacaoInicio": inicio.isoformat(),
            "dataDisponibilizacaoFim": fim.isoformat(),
            "pagina": pagina,
            "itensPorPagina": ITENS_POR_PAGINA,
        })
        if isinstance(dados, dict) and dados.get("status") not in (None, "success"):
            raise ErroConsulta(dados.get("message") or "O DJEN retornou um erro.")
        itens = (dados.get("items") if isinstance(dados, dict) else dados) or []
        if total is None and isinstance(dados, dict):
            total = dados.get("count")
        for item in itens:
            pub = normalizar(item)
            if pub["id"] not in vistos:
                vistos.add(pub["id"])
                resultados.append(pub)
        if progresso:
            progresso(len(resultados), total)
        if len(itens) < ITENS_POR_PAGINA or (total and len(resultados) >= total):
            break
        pagina += 1
        time.sleep(0.5)  # respeita o limite de requisições da API

    resultados.sort(key=lambda p: (datetime.strptime(p["data"], "%d/%m/%Y")
                                   if re.match(r"\d\d/\d\d/\d{4}$", p["data"]) else datetime.min),
                    reverse=True)
    return resultados


# --------------------------------------------------------------------------
# Exportação
# --------------------------------------------------------------------------

COLUNAS_EXPORT = [
    ("data", "Data"), ("tribunal", "Tribunal"), ("processo", "Processo"),
    ("tipo", "Tipo"), ("documento", "Documento"), ("orgao", "Órgão"),
    ("classe", "Classe"), ("partes", "Partes"), ("advogados", "Advogados"),
    ("link", "Link"), ("texto", "Teor"),
]


def exportar_csv(publicacoes, caminho):
    # utf-8-sig + ';' para abrir corretamente no Excel em português
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow([titulo for _, titulo in COLUNAS_EXPORT])
        for p in publicacoes:
            w.writerow([p.get(chave, "") for chave, _ in COLUNAS_EXPORT])


def exportar_html(publicacoes, caminho, titulo):
    blocos = []
    for p in publicacoes:
        link = f'<a href="{html.escape(p["link"])}">documento</a>' if p["link"] else ""
        blocos.append(f"""
<div class="pub">
  <h3>{html.escape(p['data'])} &middot; {html.escape(p['tribunal'])} &middot; {html.escape(p['processo'])}</h3>
  <p class="meta">{html.escape(p['tipo'])} {html.escape(p['documento'])} &middot; {html.escape(p['orgao'])}<br>
  {html.escape(p['classe'])}<br>Partes: {html.escape(p['partes'])} {link}</p>
  <pre>{html.escape(p['texto'])}</pre>
</div>""")
    conteudo = f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<title>{html.escape(titulo)}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#222}}
.pub{{border:1px solid #ccc;border-radius:6px;padding:12px 16px;margin:14px 0}}
h3{{margin:0 0 6px;font-size:16px}}.meta{{color:#555;font-size:13px;margin:0 0 8px}}
pre{{white-space:pre-wrap;font-family:inherit;font-size:14px;margin:0}}</style></head>
<body><h1>{html.escape(titulo)}</h1><p>{len(publicacoes)} publicação(ões).</p>{''.join(blocos)}</body></html>"""
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo)


# --------------------------------------------------------------------------
# Interface gráfica (Tkinter)
# --------------------------------------------------------------------------

def _parse_data(texto):
    texto = texto.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    raise ErroConsulta(f"Data inválida: '{texto}'. Use o formato DD/MM/AAAA.")


def iniciar_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    cfg = carregar_config()
    lidas = carregar_lidas()
    estado = {"pubs": [], "filtradas": []}

    root = tk.Tk()
    root.title(APP_NOME)
    root.geometry("1150x720")
    root.minsize(900, 560)
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass

    # ---- Barra de busca ----
    topo = ttk.Frame(root, padding=(10, 10, 10, 4))
    topo.pack(fill="x")

    ttk.Label(topo, text="OAB nº").grid(row=0, column=0, sticky="w")
    var_oab = tk.StringVar(value=cfg["oab"])
    ttk.Entry(topo, textvariable=var_oab, width=10).grid(row=0, column=1, padx=(4, 10))

    ttk.Label(topo, text="UF").grid(row=0, column=2, sticky="w")
    var_uf = tk.StringVar(value=cfg["uf"])
    ttk.Combobox(topo, textvariable=var_uf, values=UFS, width=4, state="readonly").grid(row=0, column=3, padx=(4, 10))

    hoje = date.today()
    ttk.Label(topo, text="De").grid(row=0, column=4, sticky="w")
    var_ini = tk.StringVar(value=(hoje - timedelta(days=int(cfg.get("dias", 7)))).strftime("%d/%m/%Y"))
    ttk.Entry(topo, textvariable=var_ini, width=11).grid(row=0, column=5, padx=(4, 10))

    ttk.Label(topo, text="Até").grid(row=0, column=6, sticky="w")
    var_fim = tk.StringVar(value=hoje.strftime("%d/%m/%Y"))
    ttk.Entry(topo, textvariable=var_fim, width=11).grid(row=0, column=7, padx=(4, 10))

    atalhos = ttk.Frame(topo)
    atalhos.grid(row=0, column=8, padx=(0, 10))

    def periodo(dias):
        var_ini.set((date.today() - timedelta(days=dias)).strftime("%d/%m/%Y"))
        var_fim.set(date.today().strftime("%d/%m/%Y"))

    for rotulo, dias in (("Hoje", 0), ("7 dias", 7), ("30 dias", 30), ("90 dias", 90)):
        ttk.Button(atalhos, text=rotulo, width=7, command=lambda d=dias: periodo(d)).pack(side="left", padx=1)

    btn_buscar = ttk.Button(topo, text="🔎 Buscar")
    btn_buscar.grid(row=0, column=9, padx=(0, 4))

    # ---- Filtro e ações ----
    barra = ttk.Frame(root, padding=(10, 2, 10, 6))
    barra.pack(fill="x")
    ttk.Label(barra, text="Filtrar resultados:").pack(side="left")
    var_filtro = tk.StringVar()
    ttk.Entry(barra, textvariable=var_filtro, width=40).pack(side="left", padx=6)
    var_so_novas = tk.BooleanVar(value=False)
    ttk.Checkbutton(barra, text="Somente não lidas", variable=var_so_novas,
                    command=lambda: aplicar_filtro()).pack(side="left", padx=6)
    btn_html = ttk.Button(barra, text="Exportar HTML/PDF")
    btn_html.pack(side="right", padx=2)
    btn_csv = ttk.Button(barra, text="Exportar Excel (CSV)")
    btn_csv.pack(side="right", padx=2)
    btn_lidas = ttk.Button(barra, text="Marcar todas como lidas")
    btn_lidas.pack(side="right", padx=2)

    # ---- Tabela + detalhe ----
    painel = ttk.PanedWindow(root, orient="vertical")
    painel.pack(fill="both", expand=True, padx=10)

    quadro_tab = ttk.Frame(painel)
    colunas = ("data", "tribunal", "processo", "tipo", "orgao", "partes")
    titulos = {"data": "Data", "tribunal": "Tribunal", "processo": "Processo",
               "tipo": "Tipo", "orgao": "Órgão", "partes": "Partes"}
    larguras = {"data": 85, "tribunal": 70, "processo": 190, "tipo": 110, "orgao": 260, "partes": 380}
    tabela = ttk.Treeview(quadro_tab, columns=colunas, show="headings", selectmode="browse")
    for c in colunas:
        tabela.heading(c, text=titulos[c], command=lambda c=c: ordenar(c))
        tabela.column(c, width=larguras[c], anchor="w", stretch=(c in ("orgao", "partes")))
    tabela.tag_configure("nova", font=("Segoe UI", 9, "bold"))
    sb = ttk.Scrollbar(quadro_tab, orient="vertical", command=tabela.yview)
    tabela.configure(yscrollcommand=sb.set)
    tabela.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    painel.add(quadro_tab, weight=3)

    quadro_det = ttk.Frame(painel)
    acoes_det = ttk.Frame(quadro_det)
    acoes_det.pack(fill="x", pady=(6, 2))
    lbl_det = ttk.Label(acoes_det, text="Selecione uma publicação para ver o teor.", font=("Segoe UI", 9, "bold"))
    lbl_det.pack(side="left")
    btn_doc = ttk.Button(acoes_det, text="Abrir documento", state="disabled")
    btn_doc.pack(side="right", padx=2)
    btn_copiar = ttk.Button(acoes_det, text="Copiar teor", state="disabled")
    btn_copiar.pack(side="right", padx=2)
    txt = tk.Text(quadro_det, wrap="word", height=12, font=("Segoe UI", 10), padx=8, pady=6)
    sb2 = ttk.Scrollbar(quadro_det, orient="vertical", command=txt.yview)
    txt.configure(yscrollcommand=sb2.set, state="disabled")
    txt.pack(side="left", fill="both", expand=True)
    sb2.pack(side="right", fill="y")
    painel.add(quadro_det, weight=2)

    var_status = tk.StringVar(value="Pronto. Fonte: DJEN / Comunica PJe (CNJ).")
    ttk.Label(root, textvariable=var_status, relief="sunken", anchor="w", padding=(8, 3)).pack(fill="x", side="bottom")

    # ---- Lógica ----
    def preencher_tabela():
        tabela.delete(*tabela.get_children())
        for i, p in enumerate(estado["filtradas"]):
            tags = () if p["id"] in lidas else ("nova",)
            tabela.insert("", "end", iid=str(i), values=[p[c] for c in colunas], tags=tags)
        novas = sum(1 for p in estado["pubs"] if p["id"] not in lidas)
        var_status.set(f"{len(estado['filtradas'])} exibida(s) de {len(estado['pubs'])} encontrada(s) "
                       f"· {novas} não lida(s) (em negrito)")

    def aplicar_filtro(*_):
        termo = var_filtro.get().strip().lower()
        estado["filtradas"] = [
            p for p in estado["pubs"]
            if (not termo or termo in " ".join(str(v) for v in p.values()).lower())
            and (not var_so_novas.get() or p["id"] not in lidas)
        ]
        preencher_tabela()

    var_filtro.trace_add("write", aplicar_filtro)

    ordem = {"col": None, "rev": False}

    def ordenar(col):
        ordem["rev"] = not ordem["rev"] if ordem["col"] == col else False
        ordem["col"] = col

        def chave(p):
            if col == "data":
                try:
                    return datetime.strptime(p["data"], "%d/%m/%Y")
                except ValueError:
                    return datetime.min
            return str(p[col]).lower()
        estado["filtradas"].sort(key=chave, reverse=ordem["rev"])
        preencher_tabela()

    def selecionada():
        sel = tabela.selection()
        return estado["filtradas"][int(sel[0])] if sel else None

    def ao_selecionar(_=None):
        p = selecionada()
        if not p:
            return
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        cab = (f"Processo: {p['processo']}   |   Tribunal: {p['tribunal']}   |   Data: {p['data']}\n"
               f"Tipo: {p['tipo']} {p['documento']}   |   Classe: {p['classe']}\n"
               f"Órgão: {p['orgao']}\nPartes: {p['partes']}\nAdvogados: {p['advogados']}\n"
               + "─" * 90 + "\n\n")
        txt.insert("1.0", cab + (p["texto"] or "(sem teor disponível)"))
        txt.configure(state="disabled")
        lbl_det.configure(text=f"Teor — {p['processo']}")
        btn_copiar.configure(state="normal")
        btn_doc.configure(state="normal" if p["link"] else "disabled")
        if p["id"] not in lidas:
            lidas.add(p["id"])
            salvar_lidas(lidas)
            tabela.item(tabela.selection()[0], tags=())

    tabela.bind("<<TreeviewSelect>>", ao_selecionar)

    def copiar():
        p = selecionada()
        if p:
            root.clipboard_clear()
            root.clipboard_append(f"Processo {p['processo']} ({p['tribunal']}) - {p['data']}\n\n{p['texto']}")
            var_status.set("Teor copiado para a área de transferência.")

    def abrir_doc():
        p = selecionada()
        if p and p["link"]:
            webbrowser.open(p["link"])

    btn_copiar.configure(command=copiar)
    btn_doc.configure(command=abrir_doc)
    tabela.bind("<Double-1>", lambda _: abrir_doc())

    def marcar_todas():
        for p in estado["pubs"]:
            lidas.add(p["id"])
        salvar_lidas(lidas)
        aplicar_filtro()

    btn_lidas.configure(command=marcar_todas)

    def nome_base():
        oab = re.sub(r"\D", "", var_oab.get())
        return f"publicacoes_OAB{oab}{var_uf.get()}_{date.today():%Y-%m-%d}"

    def exp_csv():
        if not estado["filtradas"]:
            messagebox.showinfo(APP_NOME, "Não há publicações para exportar.")
            return
        caminho = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=nome_base() + ".csv",
                                               filetypes=[("Planilha CSV (Excel)", "*.csv")])
        if caminho:
            exportar_csv(estado["filtradas"], caminho)
            var_status.set(f"Exportado: {caminho}")

    def exp_html():
        if not estado["filtradas"]:
            messagebox.showinfo(APP_NOME, "Não há publicações para exportar.")
            return
        caminho = filedialog.asksaveasfilename(defaultextension=".html", initialfile=nome_base() + ".html",
                                               filetypes=[("Página HTML", "*.html")])
        if caminho:
            titulo = f"Publicações OAB {var_oab.get()}/{var_uf.get()} — {var_ini.get()} a {var_fim.get()}"
            exportar_html(estado["filtradas"], caminho, titulo)
            webbrowser.open("file://" + os.path.abspath(caminho))
            var_status.set(f"Exportado: {caminho} (use Ctrl+P no navegador para salvar em PDF)")

    btn_csv.configure(command=exp_csv)
    btn_html.configure(command=exp_html)

    def buscar():
        try:
            inicio, fim = _parse_data(var_ini.get()), _parse_data(var_fim.get())
        except ErroConsulta as e:
            messagebox.showerror(APP_NOME, str(e))
            return
        oab, uf = var_oab.get().strip(), var_uf.get()
        cfg.update(oab=oab, uf=uf, dias=max(0, (fim - inicio).days))
        salvar_config(cfg)
        btn_buscar.configure(state="disabled")
        var_status.set("Consultando o DJEN...")

        def progresso(n, total):
            root.after(0, var_status.set, f"Consultando o DJEN... {n}" + (f" de {total}" if total else ""))

        def tarefa():
            try:
                pubs = buscar_publicacoes(oab, uf, inicio, fim, progresso)
                root.after(0, concluir, pubs, None)
            except ErroConsulta as e:
                root.after(0, concluir, None, str(e))
            except Exception as e:  # noqa: BLE001
                root.after(0, concluir, None, f"Erro inesperado: {e}")

        threading.Thread(target=tarefa, daemon=True).start()

    def concluir(pubs, erro):
        btn_buscar.configure(state="normal")
        if erro:
            var_status.set("Erro na consulta.")
            if messagebox.askyesno(APP_NOME, erro + "\n\nDeseja abrir a consulta no site do CNJ?"):
                webbrowser.open(CONSULTA_WEB)
            return
        estado["pubs"] = pubs
        aplicar_filtro()
        if not pubs:
            var_status.set("Nenhuma publicação encontrada no período.")

    btn_buscar.configure(command=buscar)
    root.bind("<Return>", lambda _: buscar())
    root.after(300, buscar)  # já busca ao abrir
    root.mainloop()


# --------------------------------------------------------------------------
# Modo terminal
# --------------------------------------------------------------------------

def main_cli(args):
    cfg = carregar_config()
    oab = args.oab or cfg["oab"]
    uf = (args.uf or cfg["uf"]).upper()
    fim = _parse_data(args.fim) if args.fim else date.today()
    inicio = _parse_data(args.inicio) if args.inicio else fim - timedelta(days=args.dias)
    print(f"Buscando publicações da OAB {oab}/{uf} de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y}...")
    try:
        pubs = buscar_publicacoes(oab, uf, inicio, fim,
                                  lambda n, t: print(f"  {n}" + (f"/{t}" if t else ""), end="\r"))
    except ErroConsulta as e:
        print(f"\nERRO: {e}")
        return 1
    print(f"\n{len(pubs)} publicação(ões) encontrada(s).\n")
    for p in pubs:
        print(f"[{p['data']}] {p['tribunal']} - {p['processo']} - {p['tipo']}")
        print(f"   {p['orgao']}")
        if p["partes"]:
            print(f"   Partes: {p['partes']}")
        resumo = p["texto"].replace("\n", " ")
        print(f"   {resumo[:300]}{'...' if len(resumo) > 300 else ''}\n")
    if args.csv:
        exportar_csv(pubs, args.csv)
        print(f"CSV salvo em: {args.csv}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=APP_NOME)
    ap.add_argument("--cli", action="store_true", help="executa no terminal, sem janela")
    ap.add_argument("--oab", help=f"número da OAB (padrão: {OAB_PADRAO})")
    ap.add_argument("--uf", help=f"UF da OAB (padrão: {UF_PADRAO})")
    ap.add_argument("--inicio", help="data inicial (DD/MM/AAAA)")
    ap.add_argument("--fim", help="data final (DD/MM/AAAA)")
    ap.add_argument("--dias", type=int, default=7, help="dias para trás, se --inicio não for informado")
    ap.add_argument("--csv", help="salva o resultado num arquivo CSV")
    args = ap.parse_args()
    if args.cli:
        sys.exit(main_cli(args))
    iniciar_gui()


if __name__ == "__main__":
    main()
