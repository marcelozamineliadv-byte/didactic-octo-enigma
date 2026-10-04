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

- Busca por OAB + UF e período (atalhos: Hoje, 7, 30 e 90 dias); já busca ao abrir.
- Lista com data, tribunal, processo, tipo, órgão e partes; clique para ler o **teor completo**.
- Publicações **não lidas em negrito** (o app lembra o que você já abriu).
- Filtro por palavra (nome da parte, nº do processo, vara...) e opção "somente não lidas".
- Abrir o documento original (duplo clique) e copiar o teor.
- Exportar para **Excel (CSV)** ou **HTML** (imprima em PDF pelo navegador com Ctrl+P).
- Lembra a OAB, a UF e o período usados.

## Rodar sem o .exe (com Python)

```
python buscador_publicacoes.py                 # janela
python buscador_publicacoes.py --cli --dias 30 # no terminal
python buscador_publicacoes.py --cli --inicio 01/09/2026 --fim 30/09/2026 --csv saida.csv
```

Para gerar o `.exe` localmente, execute `gerar_exe.bat` no Windows.

## Observações

- Fonte: API pública `comunicaapi.pje.jus.br` do CNJ. Publicações de diários que ainda não
  migraram para o DJEN podem não aparecer — confira também os diários próprios dos tribunais quando necessário.
- O app é uma ferramenta de apoio; não substitui a conferência oficial dos prazos.
