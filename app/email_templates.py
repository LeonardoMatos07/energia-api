"""The e-mails Spillway sends: layout, templates and the variables each one takes.

Energia owns its templates; Relay is generic and only fills in the variables
(docs: Relay ADR 8). Two kinds of placeholder, filled at different times:

- `[[name]]` is fixed while the template is built here (links, minutes of validity, the report
  title). It does not change from one recipient to the next.
- `{{name}}` is left in place and travels to Relay with the values for each recipient.

`render` mirrors what Relay does with `{{name}}`, so the console, SMTP and SES backends, which
have no Relay in front of them, produce the same e-mail.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "email_templates"

Variables = Mapping[str, str | int]

_RUNTIME = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_BUILD = re.compile(r"\[\[([a-z_]+)\]\]")
_BLOCK = re.compile(r"\[\[block:([a-z_]+)\]\]")


@dataclass(frozen=True)
class EmailTemplate:
    name: str
    subject: str
    text: str
    html: str
    variables: tuple[str, ...]


def render(template: str, variables: Variables, *, html: bool) -> str:
    missing = {name for name in _RUNTIME.findall(template) if name not in variables}
    if missing:
        raise KeyError(f"variables missing: {', '.join(sorted(missing))}")
    return _RUNTIME.sub(
        lambda m: escape(str(variables[m[1]])) if html else str(variables[m[1]]), template
    )


def placeholders(template: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_RUNTIME.findall(template)))


def _fill(template: str, values: Mapping[str, str], *, html: bool) -> str:
    def value(match: re.Match[str]) -> str:
        found = values[match[1]]
        return escape(found) if html else found

    return _BUILD.sub(value, template)


def _read(name: str) -> str:
    return (TEMPLATE_DIR / f"{name}.html").read_text(encoding="utf-8")


FOOTER_ACCOUNT = (
    "Você recebeu este e-mail porque tem uma conta no Spillway.<br>"
    '<a href="[[web_url]]" style="color:#52667a;">[[web_url]]</a>'
)
FOOTER_UNSUBSCRIBE = (
    "Você recebe este e-mail porque se inscreveu no Spillway.<br>"
    '<a href="{{unsubscribe_url}}" style="color:#52667a;">Cancelar inscrição</a>'
    ' · <a href="[[web_url]]" style="color:#52667a;">Spillway</a>'
)
FOOTER_MANAGE = (
    "Você recebe este e-mail porque aceitou receber novidades do Spillway.<br>"
    '<a href="[[account_url]]" style="color:#52667a;">Gerenciar preferências</a>'
    ' · <a href="[[web_url]]" style="color:#52667a;">Spillway</a>'
)


@dataclass(frozen=True)
class _Spec:
    subject: str
    preheader: str
    text: str
    footer: str


SPECS: dict[str, _Spec] = {
    "verification-code": _Spec(
        subject="Seu código do Spillway: {{code}}",
        preheader="Seu código de verificação é {{code}}.",
        text=(
            "Olá, {{name}}!\n\n"
            "Recebemos um cadastro no Spillway com este e-mail.\n"
            "Seu código de verificação é: {{code}}\n\n"
            "Digite esse código na tela de cadastro. Ele vale por [[minutes]] minutos.\n"
            "Se não foi você, ignore este e-mail: nada será ativado.\n"
        ),
        footer=FOOTER_ACCOUNT,
    ),
    "reset-code": _Spec(
        subject="Seu código para redefinir a senha: {{code}}",
        preheader="Seu código para criar uma nova senha é {{code}}.",
        text=(
            "Olá, {{name}}!\n\n"
            "Recebemos um pedido para redefinir a senha do Spillway.\n"
            "Seu código para criar uma nova senha é: {{code}}\n\n"
            "Digite esse código na tela de recuperação de senha. "
            "Ele vale por [[minutes]] minutos.\n"
            "Se não foi você, ignore este e-mail: sua senha continua a mesma.\n"
        ),
        footer=FOOTER_ACCOUNT,
    ),
    "existing-account": _Spec(
        subject="Você já tem conta no Spillway",
        preheader="Alguém tentou criar um cadastro com o seu e-mail.",
        text=(
            "Olá, {{name}}!\n\n"
            "Alguém tentou criar um cadastro no Spillway com este e-mail, que já tem conta.\n"
            "Para entrar: [[login_url]]\n"
            "Se esqueceu a senha: [[reset_url]]\n\n"
            "Se não foi você, ignore este e-mail.\n"
        ),
        footer=FOOTER_ACCOUNT,
    ),
    "newsletter-confirmation": _Spec(
        subject="Confirme sua inscrição na newsletter do Spillway",
        preheader="Falta só um clique para receber os relatórios mensais.",
        text=(
            "Olá!\n\n"
            "Recebemos um pedido de inscrição na newsletter do Spillway.\n"
            "Para confirmar, acesse (válido por 48 horas):\n{{confirm_url}}\n\n"
            "Se não foi você, ignore este e-mail.\n"
            "Para sair da lista a qualquer momento: {{unsubscribe_url}}\n"
        ),
        footer=FOOTER_UNSUBSCRIBE,
    ),
    "report": _Spec(
        subject="Relatório de [[month_label]]: [[report_title]]",
        preheader="O relatório mensal de [[month_label]] já está no ar.",
        text=(
            "Olá, {{name}}!\n\n"
            "Relatório mensal de [[month_label]]: [[report_title]]\n"
            "[[report_summary]]\n\n"
            "[[block:content]]\n\n"
            "Para cancelar a inscrição: {{unsubscribe_url}}\n"
        ),
        footer=FOOTER_UNSUBSCRIBE,
    ),
    "analysis": _Spec(
        subject="As análises de [[month_label]] estão prontas",
        preheader="Novas análises de curtailment: compensação, empresas e perfil horário.",
        text=(
            "Olá, {{name}}!\n\n"
            "As análises de curtailment de [[month_label]] estão prontas.\n\n"
            "[[block:content]]\n\n"
            "Gerenciar preferências: [[account_url]]\n"
        ),
        footer=FOOTER_MANAGE,
    ),
}

MONTHS_PT = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def month_label(year: int, month: int) -> str:
    return f"{MONTHS_PT[month - 1]} de {year}"


def _shorten(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def build(
    name: str,
    web_url: str,
    blocks: Mapping[str, tuple[str, str]] | None = None,
    **fixed: str | int,
) -> EmailTemplate:
    """The template with its `[[fixed]]` values filled and `{{per_recipient}}` ones left.

    `[[block:name]]` takes a ready-made `(html, text)` pair, such as the body of a report. The
    HTML goes in as it is, so only build blocks from content that was escaped or sanitised.
    """
    spec = SPECS[name]
    values: dict[str, str] = {
        "web_url": web_url,
        "login_url": f"{web_url}/entrar",
        "reset_url": f"{web_url}/esqueci-a-senha",
        "account_url": f"{web_url}/conta",
        "analysis_url": f"{web_url}/analises",
        **{key: str(value) for key, value in fixed.items()},
    }
    pairs = blocks or {}
    layout = _read("layout")
    html = layout.replace("[[content]]", _read(name)).replace("[[footer]]", spec.footer)
    html = html.replace("[[preheader]]", escape(spec.preheader, quote=False))
    subject_values = {**values, "report_title": _shorten(values.get("report_title", ""), 120)}
    filled_html = _fill(html, values, html=True)
    filled_text = _fill(spec.text, values, html=False)
    variables = placeholders(spec.subject + spec.text + html)
    # Blocks go in last: their content is not a template, so neither the `[[...]]` filling nor
    # Relay's `{{...}}` may touch it.
    return EmailTemplate(
        name=name,
        subject=_fill(spec.subject, subject_values, html=False),
        text=_BLOCK.sub(lambda m: _inert(pairs[m[1]][1], html=False), filled_text),
        html=_BLOCK.sub(lambda m: _inert(pairs[m[1]][0], html=True), filled_html),
        variables=variables,
    )


def _inert(fragment: str, *, html: bool) -> str:
    """The fragment with `{{` made harmless, so a report that mentions `{{name}}` is shown as
    written instead of being read as a placeholder."""
    return fragment.replace("{{", "&#123;&#123;" if html else "{​{")
