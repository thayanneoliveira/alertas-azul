# Alertas Azul

Projeto pessoal para monitorar ofertas públicas de passagens da Azul em pontos e enviar alertas por e-mail.

## Objetivo

Permitir uma regra como:

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

## Configuração no GitHub

Em `Settings > Secrets and variables > Actions`, crie:

### Secrets

- `RESEND_API_KEY`: chave da API do Resend;
- `EMAIL_TO`: endereço que receberá os alertas.

### Variables

- `ORIGIN`: `CNF`;
- `DESTINATION`: `SLZ`;
- `YEAR`: `2026`;
- `MONTH`: `9`;
- `MAX_POINTS`: `6000`;
- `EMAIL_FROM`: `Alertas Azul <onboarding@resend.dev>`.

No modo de testes do Resend, o destinatário normalmente precisa ser o mesmo e-mail cadastrado na conta. Para enviar a outros endereços, configure um domínio verificado no Resend.

## Execução automática

O workflow `.github/workflows/alertas.yml` executa o monitor aproximadamente às 08h e às 20h no horário de Brasília. O GitHub pode atrasar execuções agendadas em períodos de maior carga.

Também é possível executar manualmente em:

`Actions > Alertas Azul > Run workflow`.

O histórico de ofertas já enviadas é mantido por cache para reduzir alertas duplicados.

## Execução local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python src/azul_alert.py
```

## Segurança

O sistema não precisa da senha do Gmail. O envio é feito pela API do Resend e a chave deve permanecer apenas nos Secrets do GitHub.
