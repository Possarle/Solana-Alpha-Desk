# Verificação da primeira versão

Data: 13/10/2026. Ambiente: Linux, Python 3.12.14, Node 24.19.0. Dependências Python fixadas em requirements.lock e requirements-dev.lock; npm em package-lock.json.

## Testes automatizados

**50 testes passaram.** `pytest -q` e `ruff check app tests` executados. Há um aviso de depreciação do TestClient atual do Starlette sobre HTTPX; não é uma falha funcional. Build TypeScript/Vite concluído.

Cenários verificados:

- Market cap nulo permanece desconhecido mesmo com FDV.
- Autoridades não consultadas, concentração desconhecida e Token-2022 não recebem aprovação indevida.
- Evidência de vendas não substitui segurança.
- Risco crítico não é superado por score social/smart money.
- Best candidate separado de worth trading; NO TRADE.
- Dimensionamento por capital/liquidez/incerteza e limites de configuração.
- Endereços base58 de 32 bytes; bloqueios por rede/endereço.
- Ordem repetida idempotente, inclusive efeito financeiro único.
- Duas entradas concorrentes respeitam a posição única.
- Dados antigos, rejeições, slippage/liq e emergência bloqueiam entradas.
- Stop-loss, take-profit, trailing, tempo máximo e queda de liquidez.
- Preço ausente ou liquidez não executável mantém slot e saída pendente; recuperação tenta de novo.
- Perda diária considera marcação aberta; trava permanece após saída.
- Reconciliação detecta divergência e impede reativação indevida.
- API, autenticação, validação, evidências, carteiras, settings e WebSocket.
- Integração completa com fixtures: discovery → security → judge → shadow → stop → conciliação.
- Risco continua executando após pausar descoberta.
- Token previamente descoberto é revisitado depois de sair do feed de lançamentos.
- Cache não renova timestamp; erro HTTP não se torna sucesso; retry, circuito e prioridade da fila.
- Contas de programas/pools não viram automaticamente holders comuns.
- Transferência de token não é compra; deltas token/USDC são apenas swap provável.
- Rota Jupiter valida mints e quantidade.
- Adaptador live não envia mesmo com flag true; confirmação exige finalização sem erro.
- Reconciliação live retorna divergência de saldo explicitamente.
- Migração sobe, desce e sobe novamente em banco SQLite temporário.
- Relatório exportado marcado como simulado, sem credenciais.

Fixtures ficam somente em `backend/tests`; todos os bancos de teste são temporários e separados do banco de operação. Os exemplos de transações nos testes são sintéticos e não representam operações do mercado.

## Consultas reais realizadas

O processo de diagnóstico consultou quatro serviços, sem API paga nem carteira privada:

| Fonte | Evidência observada nesta execução |
|---|---|
| GeckoTerminal | Resposta válida com 20 pools; parser produziu preço, liquidez, volume, contagens e timestamps. Muitos market caps eram null, preservados. |
| DexScreener | Uma sondagem inicial retornou HTTP 403; a integração HTTPX posteriormente retornou 27 pares. O scanner persistiu tokens e avaliações. |
| Solana RPC | getAccountInfo do mint público de Wrapped SOL retornou jsonParsed com programa, decimals e autoridades. Avaliações adicionais também foram tentadas pelo scanner. |
| Jupiter | Consulta quote-only USDC → Wrapped SOL → USDC retornou rota, amounts e router. Nenhuma transação foi construída com taker, assinada ou transmitida. |

Foram exibidos **47 tokens reais coletados** no navegador durante a validação. Esse número descreve a execução de teste, não é um número fixo do produto e não é promessa de cobertura. O arquivo entregue não inclui banco de mercado nem posições pré-carregadas; ele começa a coletar ao iniciar.

A disponibilidade futura, cotação e frequência das fontes não são garantidas por essa consulta bem-sucedida. A migração e o serviço sob teste não usam atalhos que aprovam ausência de dados.

## Navegador

Chromium headless com backend FastAPI real e build de produção React. Verificados:

- Overview, New Tokens, Alpha Scanner, Smart Money, Wallet Tracker, AI Analysis, Trading Terminal, Open Positions, Trade History, Performance, Risk Manager e Settings.
- Pesquisa por endereço, abertura e aba de segurança no detalhe.
- Salvamento de parâmetros sem relaxar política de segurança.
- Pausa e retomada da descoberta.
- WebSocket aceito e atualização de estado.
- Layout desktop 1440 px e mobile 390 px; sem transbordamento horizontal da página (tabelas têm rolagem própria).
- Nenhum erro JavaScript de página observado.

As capturas usadas na inspeção visual eram temporárias e não integram o pacote.

## Não verificado com infraestrutura real do usuário

- Docker e servidor PostgreSQL (Docker indisponível neste ambiente).
- Windows PowerShell e Codespaces hospedado; scripts/configuração fornecidos.
- Conta Telegram/bot ou Ollama com modelo instalado.
- Lucro/prejuízo de uma carteira específica, histórico completo ou vendabilidade real de qualquer memecoin.
- Operação live: ausente e bloqueada; nunca foi executada.

Não existe evidência de lucratividade da estratégia. O padrão estrito de concentração impede entradas quando o dado não está resolvido. Os testes provam propriedades do software nos cenários cobertos, não segurança econômica dos ativos.

## Atualização Agent Desk v0.2 (2026-10-13)

- **60 testes Pytest aprovados**, incluindo revalidação do vencedor com timeout, preço
  antigo, queda de liquidez, mint divergente e nova autoridade perigosa; nenhuma dessas
  situações abre posição. Uma simulação aprovada gera plano e avaliação vinculados,
  entra e encerra por stop mesmo com a descoberta pausada; caixa reconciliado.
- Ruff e compilação TypeScript/Vite aprovados. A suíte emite um aviso de depreciação
  do adaptador Starlette/httpx, sem falhas de testes.
- Chromium com backend real: 13 telas, seleção de agente, detalhes do token, salvamento
  de configuração, pausa/retomada, WebSocket; desktop 1440 px e mobile 390 px sem
  overflow horizontal e sem erros JavaScript observados.
- Diagnóstico público nesta atualização: GeckoTerminal respondeu com 20 pares,
  DexScreener com 24, Solana RPC respondeu para Wrapped SOL. Jupiter quote-only
  respondeu com erro do provedor; disponibilidade de rotas não foi comprovada nesta rodada.
- Uma segunda execução em banco temporário vazio, com scheduler e fontes reais,
  recebeu 43 mercados. Na janela de observação de aproximadamente 90 segundos,
  29 tokens estavam persistidos e 28 avaliações haviam sido concluídas, todas rejeitadas;
  VET ainda processava a próxima consulta. A tela exibiu essa atividade e não apresentou
  erros JavaScript. A execução foi encerrada ao fim da janela; não é prova de conclusão
  de todo esse ciclo nem de entradas automáticas com ativos reais.
- Entrada, saída, planos e bloqueios foram comprovados por fixtures offline identificadas
  como testes. Nenhuma transação financeira foi assinada ou enviada. Nenhum banco de
  teste, histórico de mercado ou saldo predefinido adicional foi incluído no Git.

Os limites de concentração, dados sociais manuais, infraestrutura não testada e live
bloqueado descritos acima continuam válidos. A mesa visual não adiciona cobertura de
holders que as fontes atuais não fornecem.

## Atualização v0.3 — 2026-10-14

Esta seção descreve o estado atual e substitui as afirmações históricas de ausência absoluta de assinatura/broadcast nas versões anteriores. Live continua **desativado por padrão**, agora com caminho supervisionado separado. Nenhum swap real ou chave do usuário foi usado nesta validação.

- **103 testes Pytest aprovados**: suíte anterior preservada; assinatura criptográfica com chave efêmera sem fundos, validação de instruções/mints/fees/destinatários, negação de setup arbitrário, permissões de keypair, confirmação duplicada, expiração, alterações de configuração durante revisão, mainnet genesis completo, timeout sem reenvio, reserva persistente após reinício, orçamento diário, compra/venda, erro finalizado, divergência de saldo, monitoramento com live desativado e API autenticada.
- Migrações SQLite `base → head → base → head` e upgrade `0001 → 0002` com preservação de dado anterior verificados. Duas tabelas novas isolam livro e ordens reais.
- Ruff aprovado; TypeScript e build Vite aprovados.
- Playwright/Chromium com backend real local e banco temporário: página Live Supervision em 1440×1000 e 390×844, envio bloqueado por padrão, preparação desabilitada sem configuração, botão Reconciliar funcional e ausência de erro JavaScript. Sem dados de mercado inventados no teste visual.
- RPC público consultado **somente em leitura**: `getGenesisHash` retornou o genesis completo esperado de mainnet. Não chamou `sendTransaction` contra rede real.
- Consulta do OpenAPI oficial atual do Jupiter confirmou `/swap/v2/build`, `transactionVersion=0` e instruções separadas. O formato de instrução é decodificado com a ABI fixada indicada em `LIVE.md`.

Limitações da verificação: fluxo financeiro testado com respostas simuladas, sem rota/build autenticada nem preenchimento real Jupiter; sem PostgreSQL/Docker/Windows executados; sem auditoria independente. Um aviso de depreciação da dependência Starlette/TestClient permanece nos testes existentes; não houve falha de teste. Cobertura de enumeração de contas depende do RPC, e rotas não suportadas falham fechadas. Testes não demonstram rentabilidade ou segurança integral de protocolos externos.
