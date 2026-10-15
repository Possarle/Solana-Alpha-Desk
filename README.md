# SOLANA ALPHA DESK

Estação local de pesquisa de memecoins Solana: dados públicos reais, decisões explicáveis e operações **SHADOW**, sem carteira privada e sem IA paga. Inclui terminal real supervisionado experimental, isolado e desativado por padrão; não foi auditado nem validado com fundos reais. Veja [configuração e limites do modo real](docs/LIVE.md).

## Obter o projeto

```bash
git clone https://github.com/Possarle/Solana-Alpha-Desk.git
```

No GitHub Codespaces, abra **Code → Codespaces → Create codespace on main**. O projeto já está na pasta do repositório; execute `bash scripts/start.sh`.

## Iniciar em poucos comandos

Requisitos: Python **3.12+**, Node **22+**, npm e acesso à internet. As versões Python fixadas foram verificadas com Python 3.12/Linux. Não há assinaturas obrigatórias.

### Linux, macOS ou GitHub Codespaces

```bash
cd Solana-Alpha-Desk
bash scripts/start.sh
```

Abra **http://localhost:8000**. O script cria `.env` e ambiente virtual, instala dependências, compila o React, executa as migrações e inicia o servidor. O primeiro ciclo começa automaticamente. Dependendo das fontes/RPC, uma rodada pode demorar mais que 90 segundos; esse intervalo é contado depois que o ciclo termina.

### Windows PowerShell

```powershell
cd Solana-Alpha-Desk
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

Python 3.12 deve estar instalado no launcher `py`, e Node 22+ no PATH. Abra http://localhost:8000. O caminho Windows está fornecido, mas não foi executado neste ambiente Linux.

### Docker com SQLite

```bash
cp .env.example .env
docker compose up --build
```

Abra http://localhost:8000. Dados ficam no volume `alpha_data`. Não use `docker compose down -v` se quiser manter seu histórico.

### Docker com PostgreSQL

```bash
cp .env.example .env
# Defina POSTGRES_PASSWORD no .env antes de usar fora de um ambiente local.
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build
```

Docker/PostgreSQL foram configurados, mas não executados aqui: o ambiente de verificação não disponibiliza Docker. O banco SQLite, suas migrações e transações foram executados e testados.

### Codespaces

Incluída configuração `.devcontainer/devcontainer.json`. Execute `bash scripts/start.sh`, abra a porta 8000 na aba **Ports** e mantenha sua visibilidade **Private**. Defina `DASHBOARD_TOKEN` no `.env` para exigir uma senha de sessão no dashboard. A página e a API utilizam a mesma origem. Para desenvolvimento separado em Vite, adicione a origem externa exata a `ALLOWED_ORIGINS`.

## Como usar

1. Aguarde o scanner coletar pools do GeckoTerminal e perfis/pools do DexScreener. Ambos são fontes independentes. Não existe carga automática de tokens fictícios.
2. Overview apresenta capital simulado inicial de **US$ 10.000**, integridade das fontes, decisões e alertas. Isso não representa saldo de uma carteira.
3. Alpha Scanner permite pesquisar endereço/nome, filtrar aprovados/rejeitados e ordenar por score, liquidez ou idade. Clique numa linha para ver motivos, regras, lacunas, preços coletados e segurança.
4. Em Settings, ajuste os parâmetros de pesquisa. Todos são persistidos no banco. Salvar invalida bloqueios temporários; o próximo ciclo reavalia. Os tetos de exposição continuam limitados a 1% por posição, com uma posição simultânea.
5. Wallet Tracker cadastra até cinco **endereços públicos**. A consulta ocorre a cada 120 segundos após o ciclo. Não há carteiras de influenciadores inventadas ou previamente classificadas como rentáveis.
6. No detalhe do token, a aba Social registra conta, URL e evidência manual. Nenhum scraping do X é realizado; evidências manuais não recebem selo de autenticidade.
7. Trading Terminal permite consultar ida/volta no Jupiter e registrar uma entrada shadow **somente quando aprovada**. As entradas automáticas também passam pelos mesmos limites.
8. Risk Manager pausa entradas, tenta encerrar posições, reconcilia caixa e mostra bloqueios. **Pausar descoberta não pausa o risco.**
9. Performance apresenta P&L simulado, marcação de posições, curva patrimonial, drawdown, taxa de acerto, expectativa e custos, com exportação de relatório JSON. Sem operações, métricas ficam vazias — não são preenchidas com retornos hipotéticos.

### Por que pode aparecer NO TRADE?

NO TRADE é um resultado válido: significa que nenhum token passou por **todas** as etapas. Na v0.4, a concentração de holders passou a ser resolvida no próprio scanner, com o RPC gratuito:

1. O robô lê as 20 maiores contas do token e quem é o dono de cada uma.
2. Lê a conta de cada pool conhecido do token. Uma conta só é tratada como **cofre de liquidez** quando o endereço dela aparece dentro dos dados do pool (prova on-chain, não suposição).
3. Endereços de programa sem vínculo comprovado (travas, curvas de bonding, outros pools) **continuam contados**, por precaução.
4. O top 10 exibido é um **limite superior**: holders fora da amostra contam com o menor saldo amostrado.

Se o pool não puder ser lido, a concentração continua **desconhecida** e a política estrita bloqueia — como antes. Ainda podem aparecer muitos NO TRADE porque memecoins frequentemente falham em liquidez, market cap confirmado, idade, fluxo de vendas, permissões ou concentração. Isso é o filtro funcionando.

A opção **Exigir concentração resolvida** em Settings continua existindo para pesquisa shadow. Market cap ausente também bloqueia: FDV é mostrado separadamente e não o substitui. A idade é do pool observado, não necessariamente do mint. A descoberta cobre uma amostra dos lançamentos; não é um firehose de toda a Solana.

## Funcionalidades implementadas

- FastAPI + Pydantic; React/TypeScript + Vite + Tailwind + Recharts; WebSocket com fallback de polling.
- Adaptadores HTTPX com orçamento conservador, fila por prioridade, timeout, cache com timestamp original, retries e circuit breaker.
- Discovery independente e Copy Intelligence compartilhando a avaliação de segurança e risco.
- Reavaliação de tokens que saíram do feed de lançamentos, em lotes limitados para respeitar fontes gratuitas.
- Verificação de mint/freeze e programa; Token-2022 bloqueado por política nesta versão. Amostra de owners sem fingir distribuição completa de holders.
- Julgamento tipado, componentes de score, motivos, desconhecidos, regras e inferências locais separadas.
- Seleção com `best_candidate` e `worth_trading` independentes; `NO TRADE` é um resultado válido.
- Histórico de preços **coletados pelo sistema**; não são candles históricos reconstruídos ou cotações de exchange em tempo real.
- Shadow com custos/slippage estimados, idempotência persistente, reserva de posição atômica, limite diário, stop, alvo, trailing, tempo máximo, queda de liquidez e emergência.
- Saída pendente preserva a posição até haver condições mínimas para registrar um encerramento simulado.
- Monitoramento de carteiras com deltas de token/USDC, swaps prováveis, recorrência, coincidência em janela de 10 min e relatório parcial de inventário/custo médio.
- Migração Alembic versionada, configurações persistentes, logs JSON sem segredos e reconciliação de caixa.
- Ollama opcional, resposta tipada e prazo de 12 s; falta de modelo nunca impede regras determinísticas.
- Telegram opcional com bot próprio, ativado somente ao preencher token e chat no backend.

## O que ainda não está completo

| Área | Limitação explícita |
|---|---|
| Segurança | Sem auditoria completa de programas de pools, LP lock/burn, identificação de desenvolvedor, grafo de carteiras ou detecção comprovada de wash trading. |
| Concentração | Scanner calcula limite superior com as 20 maiores contas, excluindo só cofres comprovados nos dados do pool. Owners com várias contas pequenas, carteiras relacionadas e desenvolvedor não são identificados. Preparação live continua exigindo enumeração integral reconciliada ao supply. |
| Token-2022 | Nome das extensões observado quando o RPC retorna jsonParsed, mas transferências não são simuladas por extensão; entradas bloqueadas. |
| Carteiras | Últimas 10 assinaturas por ciclo; lacunas, histórico incompleto, transferências e posições anteriores afetam o relatório. Operações em SOL nativo sem contraparte USDC ficam não classificadas. Sem ranking de “carteiras lucrativas”. |
| Social | Formulário de evidências; idade de conta, engajamento, reciclagem e atividade de GitHub não são coletados automaticamente. Score social permanece desconhecido. |
| Simulação | Não modela ordem em livro, MEV, gas real, todos os custos de ATA/rent, transfer fees, impostos nem garantia de preenchimento. Paper e shadow compartilham um estimador; apenas shadow está exposto no dashboard. |
| Performance | Comparação “com e sem filtros” usa variação **bruta** do preço no mesmo pool (15 min, 1 h, 4 h), sem custos nem garantia de execução. Tokens que somem das fontes contam como “sem dado”, o que pode esconder rugs. Sem backtest retrospectivo; nenhum parâmetro é alterado automaticamente. |
| LIVE | Terminal supervisionado com montagem restrita, assinatura local, tentativa única de envio e reconciliação. Desativado por padrão; exige configuração explícita e confirmação por ordem. Sem swaps reais validados ou auditoria independente. Stops apenas alertam. Veja [LIVE.md](docs/LIVE.md). |
| Operação | Uma instância / um worker; não é um cluster de alta disponibilidade. Sem retenção automática de histórico; planeje backups e espaço em disco. |
| Integrações | Telegram/Ollama preparados mas não testados com credenciais/modelo do usuário; PostgreSQL/Docker não executados neste ambiente. |

## Testes

```bash
bash scripts/test.sh
```

Ou, separadamente:

```bash
.venv/bin/pip install -r backend/requirements-dev.lock
cd backend
../.venv/bin/pytest -q
../.venv/bin/ruff check app tests
cd ../frontend
npm ci
npm run build
```

A suíte é offline: respostas externas simuladas ficam **somente nos testes**, com bancos temporários. Não são inseridas no banco de operação. Veja `docs/VERIFICATION.md` para resultados de execução, consulta real e validação de interface.

## Desenvolvimento

Backend:

```bash
cd backend
../.venv/bin/alembic upgrade head
../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Frontend em outro terminal:

```bash
cd frontend
npm run dev
```

No desenvolvimento separado, copie `.env.example` para `backend/.env` ou exporte as variáveis; o script principal já carrega o `.env` da raiz. Evite `--reload` ou múltiplos workers com o scheduler de produção. Nunca execute duas instâncias sobre o mesmo banco. O lock local do scheduler não é um lock distribuído.

OpenAPI: http://localhost:8000/docs. Saúde: `/healthz`. Estado: `/api/state`. WebSocket: `/ws`. A autenticação WebSocket vai no primeiro frame, não na URL.

## Estrutura

```text
backend/app/
  agents/        discovery, security, smart_money, social, judge,
                 pick, position_size, risk, filter, bench
  providers/     HTTP resiliente, DexScreener, GeckoTerminal, Solana, Jupiter
  execution/     shadow, paper e preparação live isolada
  thresholds.py  parâmetros e validações
  schemas.py     contratos Pydantic
  db.py          16 tabelas persistentes
  cohort.py      coorte contrafactual com/sem filtros
  engine.py      ciclos independentes de descoberta, risco e carteiras
  main.py        API, autenticação e WebSocket
  performance.py relatórios estimados
backend/tests/   testes unitários, de integração e risco
backend/migrations/ migração Alembic congelada
frontend/src/    dashboard React, tipos e estilos
scripts/         início local, migração e testes
.devcontainer/   configuração Codespaces
```

## Metodologia, custos e fontes

Veja `docs/ARCHITECTURE.md`, `docs/SOURCES.md` e `docs/VERIFICATION.md`. Nenhuma configuração ou pontuação foi calibrada como probabilidade de lucro. Este pacote não é uma recomendação para comprar os tokens exibidos.


## v0.2 — Agent Desk: mesa operacional

A tela inicial **Agent Desk** acompanha CRAWLER, SCAN, VET, SOCIAL, SMART,
JUDGE, CHIEF, SIZE, FILLS e RISK. Os estados vêm do motor Python por WebSocket
(com polling de recuperação), não de uma animação com números aleatórios.
Clique em um agente para ver a responsabilidade e suas últimas atividades.
Animação aparece somente durante execução observada; conexão antiga interrompe-a.

- O token em foco acompanha a posição ou o candidato. O seletor permite investigar outro token.
- O checklist mostra valor observado, limite e resultado PASS/BLOCK/N/D da avaliação.
- O CHIEF distingue o melhor candidato, a aprovação e o resultado da tentativa de entrada.
- O SIZE exibe o último plano da decisão: orçamento, incerteza, custos, stop e alvo estimados.
- O RISK e o histórico persistente mostram acompanhamento, saídas e bloqueios.
- Antes da entrada automática, mercado e segurança são reavaliados. Falhas, mudanças
  de permissões, dados antigos ou mudança de configuração bloqueiam a tentativa.
- Decisões, planos, parâmetros usados e avaliações ficam no banco existente. Não há migração
  destrutiva. Os últimos 80 eventos de atividade são transitórios e reiniciam com o processo;
  decisões, avaliações, ordens e eventos operacionais permanecem persistidos.

### Atualizar uma instalação existente no Codespaces

No terminal que executa o servidor, pressione Ctrl+C. Depois, na raiz do repositório:

```bash
git pull --ff-only origin main
bash scripts/start.sh
```

Abra a porta 8000 e atualize o navegador. Se o Git avisar que existem alterações locais,
preserve essas alterações antes de resolver o conflito; não utilize reset destrutivo.

### Por que pode continuar sem comprar na simulação?

> Texto da v0.2, mantido como histórico. Na v0.4 a concentração passou a ser resolvida no scanner; veja a seção NO TRADE acima.

A política padrão exige concentração resolvida. A amostra de maiores contas SPL do RPC
não resolve a distribuição por holder nem identifica todos os pools/PDAs: essa checagem
continua **desconhecida** e, portanto, bloqueia entradas no perfil estrito.

Para um experimento explicitamente menos restritivo, o usuário pode desmarcar
**Exigir concentração resolvida** em Settings e salvar. Isso muda apenas a pesquisa
SHADOW: a lacuna permanece visível, o tamanho é reduzido e todas as outras verificações
continuam ativas. Não garante entradas nem comprova segurança ou rentabilidade.
A atualização não relaxa essa configuração automaticamente.

### Relação com o vídeo de referência

A implementação segue o fluxo de módulos de coleta, filtro, avaliação, seleção,
alocação, execução e risco. Não reproduz o código privado do vídeo nem seus resultados.
O visual usa estações de agentes com estados observados, não uma sala 3D.
Social é manual e opcional; Ollama é opcional e não autoriza ordens. Não há scraping do X,
conversas inventadas entre agentes ou execução live. O projeto não opera quando o
Codespaces está parado.


## v0.3 — Live Supervision

Nova página com prontidão, carteira pública, limites, revisão individual, confirmação explícita, posição e histórico reais separados do shadow. O backend persiste assinatura antes do envio, mantém reservas após timeouts e exige finalização/reconciliação para liberar posição.

Para atualizar: pare o servidor, execute `git pull --ff-only origin main` e `bash scripts/start.sh`. Nenhuma configuração é habilitada automaticamente. A migração `0002` adiciona duas tabelas e preserva o histórico existente. Siga [docs/LIVE.md](docs/LIVE.md) para a configuração e limitações; não envie chaves privadas pelo chat.


## v0.4 — Concentração resolvida e comparação com/sem filtros

**O que mudou, em linguagem simples:**

- **Concentração de holders no scanner.** Antes, o robô nunca conseguia calcular o top 10 e por isso bloqueava tudo. Agora ele separa cofres de liquidez (comprovados nos dados do pool), carteiras comuns e endereços de programa. Veja na aba **Segurança** do detalhe de cada token.
- **Com e sem filtros (Performance).** Todo token avaliado — aprovado ou rejeitado — tem o preço anotado na hora da decisão e é medido de novo em 15 min, 1 h e 4 h, sempre no mesmo pool. O painel compara aprovados × todos × rejeitados. Se os filtros ajudam, os aprovados devem ficar acima. A comparação só é marcada como suficiente com pelo menos 10 aprovados medidos em 1 h.
- **Testes em clone novo.** `bash scripts/test.sh` funciona sem banco pré-existente.

A migração `0003` adiciona a tabela `cohort` e preserva todo o histórico. Para atualizar no Codespaces: pare o servidor (Ctrl+C), rode `git pull --ff-only origin main` e `bash scripts/start.sh`.

A coorte usa no máximo uma consulta ao DexScreener por minuto (até 30 tokens), reaproveitando primeiro os preços que o scanner já coletou. Nenhum dado é preenchido quando a fonte falha.


## v0.5 — Trading Floor

Nova página inicial **Trading Floor**: a mesa inteira em uma tela, no estilo de uma sala de operações.

- **Topo:** perda do dia em relação ao limite, patrimônio simulado, dia e relógio UTC.
- **8 cartões de agentes** (CHIEF, SCAN, VET, SOCIAL, SIZE, FILLS, RISK, CRAWLER), cada um com o estado real, uma métrica e a última mensagem do agente.
- **Sala:** cada agente é um mascote numa mesa. Quando ele está trabalhando, o monitor acende, o mascote se mexe e as linhas de dados correm até o próximo agente. Quando bloqueia, o monitor fica vermelho. O balão mostra a última mensagem real.
- **Telão:** preços coletados do token em foco, com entrada, stop e alvo quando há posição shadow. Clique nas moedas para trocar o foco.
- **Saldo:** curva do patrimônio simulado e uma barra por ciclo (verde = teve candidato, vermelho = NO TRADE).
- **thresholds.py:** cada filtro com a regra, o valor observado e ✓ / ✗ / ?, além do funil `filter.py`, que mostra em que etapa o token parou.

Nada se mexe sem atividade real: sem dados, a tela mostra "aguardando". Os mascotes e a identidade visual são originais. Não há gráfico de candles reconstruído; o telão usa os preços que o robô coletou. No celular, deslize a sala para os lados. O Agent Desk antigo continua disponível no menu.

Para atualizar no Codespaces: pare o servidor (Ctrl+C), rode `git pull --ff-only origin main` e `bash scripts/start.sh`. Não há migração de banco nesta versão.


## v0.6 — Regras de compra/venda validadas e auditoria de tempo

**Correções nas regras de saída** (provadas com o motor real antes de corrigir):

1. **O stop-loss configurado nunca disparava.** O trailing stop (10%) contava desde a entrada, então qualquer queda de 10% vendia antes do stop de 12%. Agora o trailing só é armado depois que a posição sobe `trailing_activation_pct` (padrão 10%); antes disso, quem manda é o stop-loss. Assim cada regra faz o que o nome diz.
2. **Um preço atrasado forçava uma venda.** Uma cotação antiga ou um erro de rede marcava a posição para sair, e ela era vendida assim que o preço voltava, mesmo saudável. Agora, problemas só de dado (preço antigo, liquidez desconhecida, falha de consulta) são reavaliados quando o dado volta. Um gatilho real (stop, alvo, perda diária, emergência) continua pendente até a venda acontecer.

**Auditoria de tempo** (Performance → "O atraso está custando dinheiro?", ou `.venv/bin/python scripts/timing_audit.py` no terminal):

- **Atraso na compra:** quanto o preço já tinha mudado logo depois da compra simulada.
- **Passou do stop:** quanto abaixo do nível do stop a venda aconteceu, por causa do intervalo entre consultas.
- **Gatilho → venda:** segundos entre o preço cruzar o gatilho e a venda.
- **Resultado por motivo de saída:** onde o dinheiro simulado está sendo ganho ou perdido.

O relatório funciona também com o histórico antigo, porque usa os preços que o robô já coletou. Novas operações passam a registrar o preço da decisão e a idade do dado na entrada e na saída. Não há migração de banco nesta versão.


## v0.6.1 — "Por que o robô não comprou?"

Novo quadro no Trading Floor (e `.venv/bin/python scripts/why_no_trade.py` no terminal):

- **Robô ligado:** quantas horas o robô realmente rodou nas últimas 48 h e quantas vezes parou, calculado pelos ciclos gravados.
- **Regras que mais bloquearam:** contadas uma vez por token.
- **Quase aprovados:** tokens que falharam em uma única regra.
- **Aprovados que não viraram compra:** motivo do bloqueio na hora da entrada.

### Importante: o Codespace desliga sozinho

O GitHub para o Codespace após **30 minutos sem você interagir** (padrão). O robô rodando sozinho não conta como atividade. Quando o Codespace para, o robô para junto e nada é analisado nem comprado.

- Para aumentar: foto de perfil → **Settings** → **Codespaces** → **Default idle timeout** → até **240 minutos** (4 h).
- A conta gratuita inclui 120 horas de computação por mês; numa máquina de 2 núcleos isso dá cerca de 60 horas de uso. Não é suficiente para deixar o robô ligado 24 horas por dia.
- Para rodar por longos períodos, use o seu próprio computador (Windows: `powershell -ExecutionPolicy Bypass -File scripts/start.ps1`; Linux/macOS: `bash scripts/start.sh`). Ele precisa ficar ligado e conectado à internet.


## v0.7 — Launch Sniper (modo lançamento)

Novo modo que avalia moedas do **pump.fun segundos depois de criadas**. Ele roda **em paralelo** ao modo atual, numa **conta fictícia separada** de US$ 10.000, para comparar qual rende mais. Fica na página **Launch Sniper**.

**Como funciona:**

1. O robô recebe, em tempo real, cada moeda nova pelo feed gratuito do PumpPortal (`subscribeNewToken`, uma única conexão).
2. Descarta na hora as moedas em que o criador comprou mais que o limite do supply.
3. Espera `confirm_seconds` (padrão **15 s**) e lê a curva de preço do pump.fun **direto na blockchain**, junto com o mint.
4. Verifica o que é possível nesse momento:
   - mint e freeze authority revogadas;
   - programa e extensões do token (Token-2022 só com extensões de metadados);
   - quanto SOL **outras carteiras** colocaram desde a criação;
   - se o preço já subiu demais (market cap máximo);
   - o impacto da própria ordem no preço.
5. Se aprovar, simula a compra **na curva**, com a taxa documentada de 1%, e acompanha a posição a cada 8 s.
6. Sai por stop, alvo, trailing, tempo máximo ou queda de SOL na curva. Se a moeda migrar da curva, vende no primeiro preço de pool disponível.

**Limitações honestas:**

- No primeiro minuto **não existe** volume, histórico de vendas, distribuição de holders nem dados sociais. Essas lacunas aparecem como desconhecidas, nunca como aprovadas.
- O RPC público é compartilhado e verifica por padrão **6 moedas por minuto**. O pump.fun cria muito mais que isso, então é uma amostra. Com um `RPC_URL` próprio (por exemplo, o plano gratuito da Helius) dá para aumentar.
- Outros robôs compram no mesmo segundo; o preço simulado pode ser melhor que o conseguido na vida real.
- A estrutura da curva segue a documentação oficial do pump.fun e é rejeitada se vier diferente. **A conexão real com PumpPortal e com a curva não pôde ser testada no ambiente de desenvolvimento** (rede bloqueada); a primeira execução no Codespace ou no seu computador é o teste real.
- Os contadores do feed reiniciam quando o robô reinicia; posições e resultados ficam no banco.
- Para desligar: `LAUNCH_MODE=false` no `.env`, ou desmarque "Modo lançamento ligado" na página.

Fontes: [PumpPortal real-time API](https://pumpportal.fun/data-api/real-time) · [pump.fun public docs](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md)


## v0.7.1 — Revisão das regras do modo lançamento

Quatro problemas encontrados na revisão, todos reproduzidos antes de corrigir:

1. **Unidade da compra do criador.** Se o feed informasse a compra do criador em unidades brutas (6 casas decimais), o robô calcularia milhões de por cento e rejeitaria toda moeda. Agora a unidade é normalizada; valores impossíveis viram "desconhecido".
2. **Consulta desperdiçada.** Moedas sem a compra do criador no feed agora são rejeitadas sem gastar consulta à blockchain.
3. **Trava permanente.** Ao atingir a perda diária, o modo lançamento ficava parado sem como reativar. Agora há "Revisar e reativar" e "Parar modo lançamento" na página (`POST /api/launch/control`). A reativação exige caixa reconciliado.
4. **Lista poluída.** Moedas puladas por limite de consultas são só contadas, sem esconder as decisões reais.

Também foi adicionada uma auditoria com preços aleatórios (`tests/test_rule_invariants.py`): centenas de operações nos dois modos, conferindo em cada saída que o stop só vende abaixo do nível, o alvo só acima, o trailing só depois de armado, o tamanho respeita o limite e os caixas reconciliam.
