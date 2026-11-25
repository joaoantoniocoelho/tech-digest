# AGENTS.md

## Projeto
Tech Digest: pipeline self-hosted que coleta artigos de tecnologia (RSS/Atom), classifica com TypeSafe Jev (`typesafe-sdk`), ranqueia de forma determinística contra `config/interests.yaml`, deduplica e envia um digest diário por email (Resend). Também serve o site público (digest.joaoac.com) e a API de inscrição. Em produção em uma VPS (Docker Compose: um processo `app` com API e agendador, SQLite em `./data`, exposto por Cloudflare Tunnel).

## Stack e comandos
Python 3.12, stdlib-first (`http.server`, `sqlite3`, `unittest`), deps em `requirements.txt` (pip + `.venv`); ferramentas de dev em `requirements-dev.txt`.
- Instalar deps: `python -m pip install -r requirements.txt -r requirements-dev.txt`
- Rodar local: `set -a && source .env && set +a && python -m app.server` (jobs avulsos: `python -m app.main`, `app.process_daily`, `app.digest`, `app.send_digest`)
- Testes: `python -m unittest discover -s tests -v`
- Verificação completa: `scripts/check` (usa `.venv/bin` automaticamente)

## Como o Digest funciona
- **Coleta** (`app.main`, a cada 4h e 06:45): lê os feeds de `config/sources.yaml` e grava só metadados novos.
- **Classificação** (`app.process_daily`, 06:50): busca o texto, classifica com Jev, pontua de forma determinística (`app/scoring.py`). O texto não é guardado.
- **Envio** (`app.send_digest`, 07:00): `build_digest()` escolhe até 8 artigos acima da nota mínima; um email por assinante via Resend; no primeiro envio aceito, os artigos viram `delivered_at` (isso é a "edição" pública do dia) e o site é revalidado. Sem artigos, não há email.
- **Nota autoral** (`app.notes`, opcional): texto escrito pelo autor que abre uma edição. Só entra se estiver `approved`; sai uma única vez. Fluxo em `docs/editorial.md`.
- **Agendador** (`app/scheduler.py`): roda dentro do processo da API, horário de Brasília; cada slot roda uma vez (`job_runs`) e um job por vez (`pipeline_lock`).
- **Site:** `digest.joaoac.com` fica no repo `joaoac.com` (pasta `digest/`) e consome `POST /subscribe`, `GET /digests`, `GET /digests/<data>` e `/unsubscribe/<token>` desta API.

## Produção (para agents)
Leia `docs/production.md` antes de qualquer ação em produção. Resumo:
- Checkout de produção na VPS: `~/apps/tech-digest`, sempre `master` limpo. Nunca desenvolva nem edite arquivos nele; o desenvolvimento acontece em outro clone.
- Comandos dentro do container: `scripts/prod <módulo> [args]` (ex.: `scripts/prod app.backup --check`, `scripts/prod app.notes list`).
- Deploy é automático a cada push na `master` com `check` verde; manualmente, `scripts/deploy` no checkout de produção.
- **Nunca sem pedido explícito de uma pessoa:** rodar `app.send_digest` (envia email real para todos), aprovar uma nota, editar `data/digest.db`, apagar backups, mexer em `.env`.
- **Notas autorais:** o agent salva o texto que recebeu do autor, sem escrever, resumir nem traduzir. Salvar é `save`; `approve` só quando o autor mandar.
- Sem artigos no dia (`reason=no_articles`) é comportamento normal, não incidente.

## Estrutura
- `app/`: módulos planos por responsabilidade (`rss`, `content`, `classifier`, `scoring`, `digest`, `email`, `subscribers`, `server`, `scheduler`...). Entry points rodam com `python -m app.<modulo>`.
- `app/db.py`: todo o acesso ao SQLite e o schema (`init_db`, migrações incrementais por coluna). Mudança aqui exige registro em `.harness/allow-schema`.
- `config/*.yaml`: fontes, interesses e configuração do digest.
- `tests/test_<modulo>.py`: um arquivo por módulo.
- `typings/`: stubs locais para libs sem tipos (ex: `feedparser`). Prefira stub a `ignore_missing_imports`.
- `docs/`: arquitetura, operações, produção (`production.md`), fluxo editorial (`editorial.md`), scoring e roadmap; mantenha atualizados quando o comportamento mudar.
- `compose.prod.yaml`, `scripts/deploy`, `scripts/prod`: produção na VPS. `compose.yaml` é só para desenvolvimento local.

## Convenções deste repo
- Testes com `unittest` (não pytest). Cada arquivo começa com `runpy.run_path(.../_bootstrap.py)` para ajustar `sys.path`/cwd; banco em arquivo temporário com `patch.object(db, "DB_PATH", ...)`; rede/IO externos via `unittest.mock.patch`.
- Erros: exceções da stdlib (`ValueError` para entrada inválida), sem tipo Result. Falhas de artigo individual não derrubam o pipeline.
- Dados circulam como `dict` vindos do SQLite; `dataclass(frozen=True)` para valores novos (ex: `subscribers.py`).
- Não armazenar nem republicar conteúdo integral de artigos: o texto extraído é temporário.
- Sem frameworks novos (web, ORM, test runner) sem pedir.

<!-- harness:start -->
## Agent harness
Este repo usa o [agent-harness](https://github.com/joaoantoniocoelho/agent-harness).
- **Verificação:** `scripts/check` é a definição de pronto (guardrails, teste junto da mudança, format, lint, typecheck, testes, build). O CI roda o mesmo script.
- **Planos:** `docs/plans/<slug>.md` (fora do git). Issues com label `agent-ready` já são o plano aprovado.
- **Precedência:** as convenções deste arquivo e as configs do repo prevalecem sobre os defaults globais do harness.
- **Schema/migrations:** autorizações registradas em `.harness/allow-schema`; statements destrutivos sempre exigem aprovação explícita.
<!-- harness:end -->
