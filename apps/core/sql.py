"""
SQL reutilizable por las migraciones (triggers de auditoría).

La función core_auditar_cambios() se crea en core/migrations/0002_auditoria.py.
Cada módulo adjunta el trigger a sus tablas sensibles con `auditar(tabla)`.
"""


def auditar(tabla: str) -> tuple[str, str]:
    trigger = f"trg_auditoria_{tabla}"[:63]
    sql = (
        f"CREATE TRIGGER {trigger} AFTER INSERT OR UPDATE OR DELETE ON {tabla} "
        f"FOR EACH ROW EXECUTE FUNCTION core_auditar_cambios();"
    )
    reverso = f"DROP TRIGGER IF EXISTS {trigger} ON {tabla};"
    return sql, reverso
