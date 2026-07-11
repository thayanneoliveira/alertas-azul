# Alertas Azul

Projeto pessoal para monitorar ofertas públicas de passagens da Azul em pontos e enviar alertas por e-mail.

## Objetivo

Permitir o cadastro de uma regra como:

- origem: CNF;
- destino: SLZ;
- mês: setembro de 2026;
- limite: até 6.000 pontos.

Quando uma oferta pública compatível for encontrada, o sistema envia um e-mail com a data, o trecho e a quantidade de pontos.

## Limites do MVP

- consulta apenas páginas públicas;
- não realiza login na conta Azul;
- não reserva nem emite passagens;
- exige validação manual no site ou aplicativo oficial;
- os seletores HTML podem precisar de ajuste caso a Azul altere a estrutura das páginas.

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Preencha o arquivo `.env` e execute:

```bash
python src/azul_alert.py
```

## Agendamento

Para uso pessoal, execute uma ou duas vezes por dia com cron, Agendador de Tarefas do Windows ou GitHub Actions.

## Segurança

Nunca salve senha de e-mail diretamente no código. Use variáveis de ambiente e, no Gmail, uma senha de aplicativo.
