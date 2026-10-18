# AGENTS.md

## Projeto
Tech Digest: pipeline self-hosted que coleta artigos de tecnologia (RSS/Atom), classifica com TypeSafe Jev (`typesafe-sdk`), ranqueia de forma determinística contra `config/interests.yaml`, deduplica e envia um digest diário por email (Resend). Também serve o site público (digest.joaoac.com) e a API de inscrição. Em produção no Railway (um único processo, SQLite em volume persistente `/data`).

## Stack e comandos
Python 3.12, stdlib-first (`http.server`, `sqlite3`, `unittest`), deps em `requirements.txt` (pip + `.venv`); ferramentas de dev em `requirements-dev.txt`.
- Instalar deps: `python -m pip install -r requirements.txt -r requirements-dev.txt`
- Rodar local: `set -a && source .env && set +a && python -m app.server` (jobs avulsos: `python -m app.main`, `app.process_daily`, `app.digest`, `app.send_digest`)
- Testes: `python -m unittest discover -s tests -v`
- Verificação completa: `scripts/check` (usa `.venv/bin` automaticamente)

## Estrutura
- `app/`: módulos planos por responsabilidade (`rss`, `content`, `classifier`, `scoring`, `digest`, `email`, `subscribers`, `server`, `scheduler`...). Entry points rodam com `python -m app.<modulo>`.
- `app/db.py`: todo o acesso ao SQLite e o schema (`init_db`, migrações incrementais por coluna). Mudança aqui exige registro em `.harness/allow-schema`.
- `config/*.yaml`: fontes, interesses e configuração do digest.
- `tests/test_<modulo>.py`: um arquivo por módulo.
- `typings/`: stubs locais para libs sem tipos (ex: `feedparser`). Prefira stub a `ignore_missing_imports`.
- `docs/`: arquitetura, operações, scoring e roadmap; mantenha atualizados quando o comportamento mudar.

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
