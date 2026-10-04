# Buscador de Publicações OAB

App para Windows que busca automaticamente as **publicações e intimações** vinculadas à sua OAB
no **DJEN – Diário de Justiça Eletrônico Nacional** (Comunica PJe / CNJ), que reúne as
comunicações processuais dos tribunais do país.

Configurado por padrão para a **OAB 517.745/SP** (pode ser alterado na própria tela).

## Como baixar o executável (.exe)

1. Acesse a aba **Releases** deste repositório no GitHub.
2. Baixe o arquivo `BuscadorPublicacoesOAB.exe`.
3. Dê dois cliques. Se o Windows exibir "O Windows protegeu o computador", clique em
   **Mais informações → Executar assim mesmo** (o aviso aparece porque o app não tem assinatura digital paga).

O executável é gerado automaticamente pelo GitHub Actions a cada atualização do código.

## Funcionalidades

O app abre em uma janela própria (usa o Microsoft Edge, que já vem no Windows, em modo aplicativo — nada a instalar).

- Busca por OAB + UF e período (atalhos: Hoje, 7, 30 e 90 dias); já busca ao abrir.
- Lista agrupada por dia (Hoje, Ontem, …) com tribunal, tipo, processo, vara e partes.
- Painel de leitura com o **teor completo**, dados do processo e **alerta de prazo** quando o texto menciona um (ex.: "15 dias úteis").
- Publicações **não lidas** marcadas com um ponto azul; o app lembra o que você já abriu.
- Filtro instantâneo (Ctrl+K) por parte, processo, vara ou texto, com destaque do termo; filtros por tribunal e "não lidas".
- Navegação pelo teclado (↑ ↓, Enter abre o documento).
- Copiar teor ou nº do processo, abrir o documento original.
- Exportar para **Excel** e **Imprimir / salvar em PDF**.
- Tema claro e escuro.

## Rodar sem o .exe (com Python)

```
python buscador_publicacoes.py                 # abre o app
python buscador_publicacoes.py --cli --dias 30 # no terminal
python buscador_publicacoes.py --cli --inicio 01/09/2026 --fim 30/09/2026 --csv saida.csv
```

Para gerar o `.exe` localmente, execute `gerar_exe.bat` no Windows.

## Observações

- Fonte: API pública `comunicaapi.pje.jus.br` do CNJ. Publicações de diários que ainda não
  migraram para o DJEN podem não aparecer — confira também os diários próprios dos tribunais quando necessário.
- O app é uma ferramenta de apoio; não substitui a conferência oficial dos prazos.
