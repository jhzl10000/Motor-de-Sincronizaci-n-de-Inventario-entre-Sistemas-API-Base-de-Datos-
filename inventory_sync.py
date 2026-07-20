"""
Inventory Sync — Sincronización de Inventario vía API
-------------------------------------------------------
Sincroniza el catálogo de productos de una API externa (tienda online, ERP,
marketplace, etc.) contra una base de datos local (SQLite), detectando
productos nuevos, productos con cambios de precio/stock, y productos que
ya no existen en el origen.

Diseñado para funcionar como el "motor" de sincronización entre dos sistemas,
por ejemplo: API de un e-commerce (Shopify/WooCommerce/Mercado Libre) <-> base
de datos interna de un ERP o sistema de gestión de inventario.

Uso:
    python inventory_sync.py                      # sincroniza usando la API por defecto
    python inventory_sync.py --api-url <url>       # sincroniza contra otra API
    python inventory_sync.py --report              # solo muestra el estado actual, sin sincronizar
    python inventory_sync.py --history              # muestra el historial de sincronizaciones

Por defecto usa https://fakestoreapi.com/products, una API pública gratuita
de prueba (sin necesidad de credenciales), ideal para mostrar el funcionamiento
sin exponer datos de un cliente real.

Autor: [Jahaziel]
"""

import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import datetime

import requests

DB_PATH = "data/inventory.db"
API_URL_DEFAULT = "https://fakestoreapi.com/products"

HEADERS = {"User-Agent": "InventorySync/1.0"}


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS productos (
    id_externo   INTEGER PRIMARY KEY,
    titulo       TEXT NOT NULL,
    categoria    TEXT,
    precio       REAL NOT NULL,
    stock        INTEGER,
    ultima_sync  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historial_sync (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha         TEXT NOT NULL,
    nuevos        INTEGER NOT NULL,
    actualizados  INTEGER NOT NULL,
    sin_cambios   INTEGER NOT NULL,
    eliminados    INTEGER NOT NULL,
    errores       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cambios_precio (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    id_externo    INTEGER NOT NULL,
    titulo        TEXT NOT NULL,
    precio_antes  REAL NOT NULL,
    precio_ahora  REAL NOT NULL,
    fecha         TEXT NOT NULL
);
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# Extracción de datos desde la API
# ---------------------------------------------------------------------------

def fetch_productos_api(api_url: str, timeout: int = 10) -> list[dict]:
    """
    Descarga el catálogo desde la API externa y lo normaliza a un formato
    interno común: {id_externo, titulo, categoria, precio, stock}.

    NOTA: cada API tiene su propio formato de respuesta. Esta función asume
    el formato de fakestoreapi.com (lista de objetos con id/title/price/
    category). Para conectar con otra API (Shopify, WooCommerce, un ERP
    propio, etc.) solo hay que ajustar el mapeo de campos aquí abajo.
    """
    resp = requests.get(api_url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    datos_crudos = resp.json()

    productos = []
    for item in datos_crudos:
        productos.append({
            "id_externo": item["id"],
            "titulo": item["title"],
            "categoria": item.get("category", "sin categoría"),
            "precio": float(item["price"]),
            # Esta API de prueba no expone stock real, así que se simula
            # a partir del rating.count (en una API real, este campo vendría
            # directamente como "stock", "quantity", "inventory_count", etc.)
            "stock": int(item.get("rating", {}).get("count", 0)),
        })
    return productos


# ---------------------------------------------------------------------------
# Lógica de sincronización (upsert + detección de cambios)
# ---------------------------------------------------------------------------

def sincronizar(conn: sqlite3.Connection, productos_api: list[dict]) -> dict:
    """
    Compara los productos obtenidos de la API contra la base de datos local:
    - Si el producto no existe -> lo inserta (nuevo)
    - Si existe y cambió precio/stock -> lo actualiza y registra el cambio
    - Si existe y no cambió -> no hace nada
    - Si existía en la BD pero ya no vino en la API -> se marca como eliminado

    Devuelve un resumen con los contadores de cada caso.
    """
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    resumen = {"nuevos": 0, "actualizados": 0, "sin_cambios": 0, "eliminados": 0, "errores": 0}

    ids_api = {p["id_externo"] for p in productos_api}

    with closing(conn.cursor()) as cur:
        for p in productos_api:
            try:
                cur.execute(
                    "SELECT precio, stock FROM productos WHERE id_externo = ?",
                    (p["id_externo"],),
                )
                fila = cur.fetchone()

                if fila is None:
                    cur.execute(
                        """INSERT INTO productos
                           (id_externo, titulo, categoria, precio, stock, ultima_sync)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (p["id_externo"], p["titulo"], p["categoria"],
                         p["precio"], p["stock"], ahora),
                    )
                    resumen["nuevos"] += 1

                else:
                    precio_antes, stock_antes = fila
                    cambio_precio = abs(precio_antes - p["precio"]) > 0.001
                    cambio_stock = stock_antes != p["stock"]

                    if cambio_precio or cambio_stock:
                        cur.execute(
                            """UPDATE productos
                               SET titulo=?, categoria=?, precio=?, stock=?, ultima_sync=?
                               WHERE id_externo=?""",
                            (p["titulo"], p["categoria"], p["precio"], p["stock"],
                             ahora, p["id_externo"]),
                        )
                        if cambio_precio:
                            cur.execute(
                                """INSERT INTO cambios_precio
                                   (id_externo, titulo, precio_antes, precio_ahora, fecha)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (p["id_externo"], p["titulo"], precio_antes, p["precio"], ahora),
                            )
                        resumen["actualizados"] += 1
                    else:
                        resumen["sin_cambios"] += 1

            except Exception as e:
                print(f"  [Aviso] Error procesando producto {p.get('id_externo')}: {e}")
                resumen["errores"] += 1

        # Detectar productos que desaparecieron del origen
        cur.execute("SELECT id_externo FROM productos")
        ids_en_bd = {row[0] for row in cur.fetchall()}
        ids_faltantes = ids_en_bd - ids_api
        resumen["eliminados"] = len(ids_faltantes)
        for id_faltante in ids_faltantes:
            cur.execute("DELETE FROM productos WHERE id_externo = ?", (id_faltante,))

        cur.execute(
            """INSERT INTO historial_sync
               (fecha, nuevos, actualizados, sin_cambios, eliminados, errores)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ahora, resumen["nuevos"], resumen["actualizados"],
             resumen["sin_cambios"], resumen["eliminados"], resumen["errores"]),
        )

    conn.commit()
    return resumen


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------

def imprimir_resumen(resumen: dict):
    print("\n📊 Resultado de la sincronización:")
    print(f"   🆕 Nuevos:        {resumen['nuevos']}")
    print(f"   🔄 Actualizados:  {resumen['actualizados']}")
    print(f"   ✅ Sin cambios:   {resumen['sin_cambios']}")
    print(f"   🗑️  Eliminados:    {resumen['eliminados']}")
    print(f"   ⚠️  Errores:       {resumen['errores']}")


def mostrar_reporte_actual(conn: sqlite3.Connection):
    with closing(conn.cursor()) as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(stock),0), COALESCE(AVG(precio),0) FROM productos")
        total, stock_total, precio_prom = cur.fetchone()
        print(f"\n📦 Estado actual del inventario:")
        print(f"   Total de productos: {total}")
        print(f"   Stock total:        {stock_total}")
        print(f"   Precio promedio:    ${precio_prom:.2f}")

        print(f"\n   Top 5 por precio:")
        cur.execute("SELECT titulo, precio, stock FROM productos ORDER BY precio DESC LIMIT 5")
        for titulo, precio, stock in cur.fetchall():
            print(f"     - {titulo[:50]:<50} ${precio:>8.2f}  (stock: {stock})")


def mostrar_historial(conn: sqlite3.Connection):
    with closing(conn.cursor()) as cur:
        cur.execute("""SELECT fecha, nuevos, actualizados, sin_cambios, eliminados, errores
                       FROM historial_sync ORDER BY id DESC LIMIT 10""")
        filas = cur.fetchall()
        print("\n🕓 Últimas sincronizaciones:")
        for fecha, nuevos, act, sin_c, elim, err in filas:
            print(f"   {fecha} | nuevos: {nuevos:>3} | actualizados: {act:>3} | "
                  f"sin cambios: {sin_c:>3} | eliminados: {elim:>3} | errores: {err:>2}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Inventory Sync — sincronización de inventario vía API")
    parser.add_argument("--api-url", default=API_URL_DEFAULT, help="URL de la API de productos")
    parser.add_argument("--db-path", default=DB_PATH, help="Ruta del archivo SQLite")
    parser.add_argument("--report", action="store_true", help="Solo muestra el estado actual, sin sincronizar")
    parser.add_argument("--history", action="store_true", help="Muestra el historial de sincronizaciones")
    args = parser.parse_args()

    conn = get_connection(args.db_path)

    if args.history:
        mostrar_historial(conn)
        return
    if args.report:
        mostrar_reporte_actual(conn)
        return

    print(f"Sincronizando inventario desde: {args.api_url}")
    productos = fetch_productos_api(args.api_url)
    print(f"Productos obtenidos de la API: {len(productos)}")

    resumen = sincronizar(conn, productos)
    imprimir_resumen(resumen)
    mostrar_reporte_actual(conn)


if __name__ == "__main__":
    sys.exit(main())
