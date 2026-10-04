"""
Buscador de Publicações e Intimações - OAB
==========================================

Consulta o DJEN (Diário de Justiça Eletrônico Nacional / Comunica PJe - CNJ)
pela API pública e lista as publicações/intimações vinculadas a uma OAB.

Uso:
    python buscador_publicacoes.py            -> abre a janela do aplicativo
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
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_NOME = "Buscador de Publicações OAB"
API_URL = "https://comunicaapi.pje.jus.br/api/v1/comunicacao"
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


# --------------------------------------------------------------------------
# Interface (página local aberta como janela de aplicativo no Edge/Chrome)
# --------------------------------------------------------------------------

def _parse_data(texto):
    texto = texto.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    raise ErroConsulta(f"Data inválida: '{texto}'. Use o formato DD/MM/AAAA.")


def _recurso(nome):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nome)


def criar_servidor(porta=0):
    """Servidor HTTP local que entrega a interface e a API usada por ela."""
    estado = {"ultimo_ping": time.time()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _responder(self, codigo, corpo, tipo="application/json; charset=utf-8"):
            if not isinstance(corpo, bytes):
                corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)

        def _corpo(self):
            tamanho = int(self.headers.get("Content-Length") or 0)
            if not tamanho:
                return {}
            try:
                return json.loads(self.rfile.read(tamanho).decode("utf-8"))
            except ValueError:
                return {}

        def do_GET(self):
            estado["ultimo_ping"] = time.time()
            rota = urllib.parse.urlparse(self.path).path
            if rota in ("/", "/index.html"):
                with open(_recurso("ui.html"), "rb") as f:
                    self._responder(200, f.read(), "text/html; charset=utf-8")
            elif rota == "/api/config":
                self._responder(200, carregar_config())
            else:
                self._responder(404, {"erro": "não encontrado"})

        def do_POST(self):
            estado["ultimo_ping"] = time.time()
            rota = urllib.parse.urlparse(self.path).path
            dados = self._corpo()
            if rota == "/api/ping":
                self._responder(200, {"ok": True})
            elif rota == "/api/buscar":
                try:
                    inicio = _parse_data(str(dados.get("inicio", "")))
                    fim = _parse_data(str(dados.get("fim", "")))
                    oab, uf = str(dados.get("oab", "")), str(dados.get("uf", UF_PADRAO))
                    pubs = buscar_publicacoes(oab, uf, inicio, fim)
                    cfg = carregar_config()
                    cfg.update(oab=re.sub(r"\D", "", oab), uf=uf.upper(), dias=max(0, (fim - inicio).days))
                    salvar_config(cfg)
                    lidas = carregar_lidas()
                    for p in pubs:
                        p["lida"] = p["id"] in lidas
                    self._responder(200, {"publicacoes": pubs})
                except ErroConsulta as e:
                    self._responder(200, {"erro": str(e)})
                except Exception as e:  # noqa: BLE001
                    self._responder(500, {"erro": f"Erro inesperado: {e}"})
            elif rota == "/api/lidas":
                lidas = carregar_lidas()
                ids = {str(i) for i in dados.get("ids", [])}
                if dados.get("lida", True):
                    lidas |= ids
                else:
                    lidas -= ids
                salvar_lidas(lidas)
                self._responder(200, {"ok": True})
            else:
                self._responder(404, {"erro": "não encontrado"})

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Handler)
    servidor.daemon_threads = True
    return servidor, estado


def _navegador_app():
    """Localiza o Edge (presente em todo Windows 10/11) ou o Chrome."""
    candidatos = []
    for var in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidatos += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    candidatos += ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    for c in candidatos:
        if os.path.isfile(c):
            return c
    for nome in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        caminho = shutil.which(nome)
        if caminho:
            return caminho
    return None


def iniciar_gui():
    servidor, estado = criar_servidor()
    url = f"http://127.0.0.1:{servidor.server_address[1]}/"
    threading.Thread(target=servidor.serve_forever, daemon=True).start()

    navegador = _navegador_app()
    processo = None
    if navegador:
        perfil = os.path.join(pasta_dados(), "janela")
        try:
            processo = subprocess.Popen([
                navegador, f"--app={url}", f"--user-data-dir={perfil}",
                "--window-size=1320,840", "--no-first-run", "--no-default-browser-check",
                "--disable-features=Translate",
            ])
        except OSError:
            processo = None
    if processo is None:
        webbrowser.open(url)

    inicio = time.time()
    if processo is not None:
        processo.wait()
        # com perfil próprio, o processo só termina quando a janela é fechada
        if time.time() - inicio > 5:
            servidor.shutdown()
            return
    # fallback: encerra quando a página deixa de dar sinal de vida
    while time.time() - estado["ultimo_ping"] < 120:
        time.sleep(5)
    servidor.shutdown()


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
