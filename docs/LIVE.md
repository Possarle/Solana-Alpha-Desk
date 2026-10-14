# Modo real supervisionado — v0.3

**Desativado por padrão. Implementação experimental, sem auditoria independente ou swap com fundos reais validado nesta entrega.** O scanner permanece em SHADOW. A página **Live Supervision** possui livro, ordens e controles próprios. Nenhuma descoberta, IA, stop ou retomada do servidor envia uma compra/venda automaticamente.

## Fluxo implementado

1. Selecione um token real já coletado e informe a entrada em USDC. **Preparar revisão** não assina.
2. O backend consulta mainnet, permissões, concentração integral, mercado, saldo e Jupiter `/swap/v2/build`. Monta a transação localmente e simula seus efeitos no RPC.
3. O painel apresenta carteira, mints, quantidades, saída mínima, slippage, impacto, taxas, aluguel de conta, incertezas e validade (45 s por padrão).
4. Para enviar é necessário habilitar a configuração no backend, digitar a frase individual da ordem e pressionar **Confirmar e enviar ordem REAL**. A revisão expirada, alterada ou com saldo/configuração divergente não pode ser enviada.
5. O backend verifica novamente a simulação, assina localmente e persiste assinatura e reserva **antes** da única tentativa de envio. Aguarda `finalized`, busca metadados e reconcilia saldos. RPC indisponível/timeout não libera reserva nem dispara novo envio.
6. A posição real aparece apenas após confirmação/reconciliação. **Preparar revisão da venda total** repete o fluxo com os mints invertidos. A posição só é liberada após o encerramento reconciliado.

## Configuração inicial: preparar sem enviar

Execute `git pull --ff-only origin main` e `bash scripts/start.sh`. O script instala dependências e aplica a migração `0002`, preservando os registros shadow.

No `.env` **do backend**:

```dotenv
LIVE_ENABLED=false
LIVE_ACKNOWLEDGEMENT=
LIVE_KEYPAIR_PATH=
LIVE_WALLET_ADDRESS=ENDERECO_PUBLICO_DA_CARTEIRA_DEDICADA
DASHBOARD_TOKEN=SEGREDO_ALEATORIO_COM_PELO_MENOS_32_CARACTERES
JUPITER_API_KEY=SUA_CHAVE_DO_PORTAL_JUPITER
ENABLE_SCHEDULER=true
```

Use valores próprios, não os exemplos acima. Gere uma senha aleatória localmente, por exemplo com `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Insira essa senha no login do dashboard. Não envie a senha, chave privada ou seed para conversas, logs ou GitHub. Chave do Jupiter fica apenas no backend; verifique disponibilidade e limites do plano gratuito no portal oficial. Nenhuma assinatura paga é instalada pelo projeto.

O RPC deve ser mainnet e aceitar `getProgramAccounts`, `simulateTransaction`, `getMultipleAccounts`, `getTransaction` e os demais métodos usados. O endpoint público pode negar a enumeração integral, limitar requisições ou ser insuficiente. Nessas condições o sistema mostra bloqueio; **não afrouxa a segurança automaticamente**. Cotação também depende da disponibilidade do Jupiter e de uma rota suportada.

A preparação consulta o saldo de uma carteira dedicada: USDC SPL na conta associada padrão (ATA), e SOL para taxas/aluguel. Uma carteira sem saldo pode ser configurada para verificar bloqueios, mas não produzirá revisão executável. Não é necessário configurar uma chave privada para observar o scanner ou preparar uma revisão.

## Habilitação explícita do envio pelo operador

Esta entrega **não ativa** nenhuma das opções abaixo. Faça a configuração somente em ambiente sob seu controle e após revisar os limites e o comportamento do código.

- Linux/macOS com permissões POSIX; assinatura nativa no Windows não é suportada nesta versão.
- Carteira dedicada, distinta de carteiras pessoais e de custódia. O sistema não cria nem financia carteiras automaticamente.
- Arquivo JSON de keypair Solana (64 bytes), fora do repositório, de propriedade do usuário que executa o backend, com permissão `600`. Diretório privado recomendado. A carteira pública deve corresponder à chave. Hardware wallets/assinatura via extensão não estão implementadas.
- Configure `LIVE_KEYPAIR_PATH` com caminho absoluto para esse arquivo. Não cole seu conteúdo no `.env`.
- Defina `LIVE_ACKNOWLEDGEMENT=I_ACCEPT_SUPERVISED_MAINNET` e `LIVE_ENABLED=true`, reinicie o backend e confira os bloqueios no terminal. Essas flags **não autorizam ordens automaticamente**: cada compra/venda continua dependendo de confirmação humana.
- Mantenha uma única instância/worker, scheduler ativo e dashboard privado. Codespaces é adequado para shadow; o envio guarda chave no servidor e exige avaliar a confiança no ambiente remoto. O Docker padrão não monta nenhuma chave; eventual montagem privada fora de `/app` precisa respeitar o UID do processo e permissões. Esse caminho Docker/live não foi validado nesta entrega.

## Limites e custos

| Controle | Padrão / comportamento |
|---|---|
| Entrada | Menor entre 10 USDC, 1% do caixa USDC e 0,05% da liquidez; redução pela metade com lacunas na avaliação |
| Posições | Uma posição real, mais uma ordem ativa de entrada/saída; independente da posição shadow |
| Orçamento diário | Compras acumuladas até o menor entre 3% e `max_daily_loss_pct` do caixa inicial do dia UTC; reservas não retornam após vendas/falhas |
| Taxa SOL | Até 50.000 lamports por ordem; reserva conservadora diária até 500.000; aluguel de ATA separado até 2.100.000 por ordem |
| Slippage / impacto | Até 100 bps / 1% por padrão; controles adicionais do dashboard podem restringir mais |
| Saída | Venda total; sem parcial, sem alavancagem; também requer taxas disponíveis e revisão |
| Retorno real | Delta USDC de venda menos custo USDC; taxas SOL mostradas separadamente, sem inventar conversão USD |

O orçamento diário limita novas compras; não representa garantia de perda máxima da posição aberta. Stops usam preço público em USD comparado ao custo unitário em USDC (aproximação sujeita ao depeg). Podem alertar por stop, alvo, trailing, tempo, queda de liquidez, falha de preço ou divergência de saldo. **Não há venda automática no modo supervisionado**, nem garantia de saída por preço específico. O monitoramento permanece ativo com descoberta pausada e `LIVE_ENABLED=false`, desde que o backend/scheduler estejam rodando.

## Reconciliação e recuperação

- Use **Reconciliar** para consultar novamente a assinatura já registrada. Não assina ou reenvia.
- `pending`: envio pode ter ocorrido; preserve o banco e acompanhe a assinatura. Status nulo não prova falha.
- `reconciliation_required` ou aviso de saldo: entradas bloqueadas e reserva preservada. Transferências externas ou negociações por outra aplicação exigem revisão técnica do histórico; não existe botão para ignorar a divergência.
- Transação finalizada com erro e saldos compatíveis registra a taxa e libera apenas a reserva de ordem; posição existente continua aberta.
- `preparing`/`validating` sem assinatura após reinício pode ser cancelada. Ordem com assinatura não pode ser cancelada pela aplicação.
- **Bloquear entradas reais** não vende nem cancela uma transação já enviada. **Revisar e reativar** não zera saldos, perdas ou orçamento diário, e recusa ordens pendentes/avisos de reconciliação.
- Se o envio não chegou ao RPC ou o blockhash expirou, a reserva pode permanecer bloqueada. A versão não tenta resolver automaticamente uma ausência de transação; requer revisão técnica. Não apague banco/ordens para contornar a trava.
- Depósitos, retiradas ou operações externas posteriores à inicialização divergem do livro e bloqueiam novas ordens. Não há importação automática desses movimentos.

## Escopo restrito de segurança

A concentração é um **limite superior conservador**: agrupa todas as contas SPL por owner e exige que a soma reconcilie o supply de mint sem autoridades. Inclui pools/PDAs no cálculo sem chamá-los de pessoas. Não identifica beneficiários, desenvolvedor, carteiras relacionadas ou LP lock. Pools grandes podem causar rejeições conservadoras. Falhas, amostra incompleta ou mais de 50 mil contas bloqueiam a análise.

A montagem aceita somente rotas Jupiter `route_v2`/`shared_accounts_route_v2`, ExactIn, tokens SPL clássicos, sem fees de plataforma/positive slippage, tips, cleanup ou instruções arbitrárias. Setup é restrito a ATA idempotente da própria carteira para os dois mints. Não usa tabelas de endereços externas; rotas acima de 1232 bytes são rejeitadas. Token-2022, SOL nativo direto, RFQ e versões novas de ABI não estão habilitados. Essa validação não substitui auditoria do Jupiter nem dos programas de pools.

Referências técnicas:
- https://developers.jup.ag/docs/swap/build/index
- https://developers.jup.ag/docs/openapi-spec/swap/v2/swap.yaml
- https://solana.com/docs/rpc/http/sendtransaction
- https://solana.com/docs/rpc/http/simulatetransaction
- https://solana.com/docs/rpc/http/getsignaturestatuses
- https://solana.com/docs/rpc/http/getprogramaccounts
- ABI em `app/execution/jupiter_idl.json`: subset de instruções + definições de tipos do repositório oficial `jup-ag/rfq-v2-sdk`, commit `f3f30ff2af48f63c11f326a486b8b0eb9611714f`, arquivo `fill-decoder/idls/aggregator.json` (blob `041ecf1cce7ca3326c2e925aa58e557b247a0ba9`). Não atualize a ABI silenciosamente; mudanças devem passar por revisão e testes.
