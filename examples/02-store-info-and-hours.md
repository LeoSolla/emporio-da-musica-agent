# General store information: address, opening hours and payment

Model: `claude-sonnet-5-5` · store clock: 2026-02-20 10:00 (Friday)

**Cliente:** Qual o endereço da loja?

> Tools: `get_store_policy({"sections": ["sobre_a_loja"]})`

**Assistente:** A gente fica na Rua 14 de Maio, 3200, Centro, Campo Grande - MS, CEP 79202-333.

Se precisar falar com a gente, o telefone é (67) 3341-4444 e o WhatsApp é (67) 3321-4500.

Quer que eu veja o horário de funcionamento ou ajude com algum instrumento?

---

**Cliente:** Vocês estão abertos agora? E no sábado?

> Tools: `get_store_hours_status({})`, `get_store_policy({"sections": ["horario"]})`

**Assistente:** Estamos abertos agora, sim! Hoje a gente fecha às 18h.

No sábado abrimos das 9h às 13h. De segunda a sexta é das 9h às 18h, e domingo e feriados ficamos fechados.

Posso te ajudar com mais alguma coisa?

---

**Cliente:** Aceitam cartão? Dá pra parcelar?

> Tools: `get_store_policy({"sections": ["pagamento"]})`

**Assistente:** Aceitamos cartão e dá pra parcelar, sim.

- Crédito: até 12x sem juros, com estas regras:
  - Até 3x: sem valor mínimo de parcela, só não pode ficar abaixo de R$ 50,00.
  - 4x a 6x: parcela mínima de R$ 80,00.
  - 7x a 12x: parcela mínima de R$ 100,00.
- Débito: à vista, todas as bandeiras.
- PIX: à vista, com 5% de desconto sobre o preço de tabela. Esse desconto não se acumula com promoções.
- Boleto: à vista, compensa em até 3 dias úteis.

Também dá pra combinar formas de pagamento, por exemplo PIX + cartão, em compras acima de R$ 2.000,00.

Quer que eu veja o preço de algum instrumento pra você?
