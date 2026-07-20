# Inventory Sync — Sincronización de Inventario vía API

Motor de sincronización en Python que conecta el catálogo de una API externa
(tienda online, marketplace, ERP) contra una base de datos local (SQLite),
detectando automáticamente productos nuevos, cambios de precio/stock, y
productos descontinuados.

## ¿Qué problema resuelve?

Cuando un negocio vende en varios canales (tienda propia, marketplaces, ERP
interno), el inventario se desincroniza fácilmente: un producto sube de precio
en un sistema pero no en otro, o se agota el stock y nadie se entera a tiempo.
Este proyecto automatiza esa sincronización y deja un **historial auditable**
de cada corrida y de cada cambio de precio detectado.

## ¿Qué hace exactamente?

1. Consulta una API externa y obtiene el catálogo actual de productos.
2. Compara cada producto contra la base de datos local:
   - **Nuevo** → lo inserta.
   - **Cambió precio o stock** → lo actualiza y registra el cambio en
     una tabla de auditoría (`cambios_precio`).
   - **Sin cambios** → no toca nada (eficiente, no reescribe todo).
   - **Ya no existe en el origen** → lo elimina de la base local.
3. Registra cada sincronización en una tabla de historial (`historial_sync`),
   con conteo de nuevos/actualizados/eliminados/errores.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

```bash
# Sincronizar con la API de prueba por defecto (fakestoreapi.com)
python inventory_sync.py

# Sincronizar con otra API
python inventory_sync.py --api-url https://mi-tienda.com/api/productos

# Ver el estado actual del inventario sin sincronizar
python inventory_sync.py --report

# Ver el historial de las últimas sincronizaciones
python inventory_sync.py --history
```

## Estructura de la base de datos (SQLite)

- **`productos`** — snapshot actual del inventario (id, título, categoría,
  precio, stock, fecha de última sincronización).
- **`cambios_precio`** — historial de todos los cambios de precio detectados
  (útil para análisis de variación de precios en el tiempo).
- **`historial_sync`** — bitácora de cada corrida de sincronización.

## Adaptar a una API real (Shopify, WooCommerce, Mercado Libre, ERP propio)

Cada API tiene su propio formato de respuesta y su propio método de
autenticación. Para conectar con una API real solo hay que:

1. Ajustar la función `fetch_productos_api()` en `inventory_sync.py`:
   - Cambiar el mapeo de campos (`item["title"]`, `item["price"]`, etc.)
     según el JSON que devuelva la API real.
   - Agregar autenticación si la API la requiere, por ejemplo:
     ```python
     headers = {"Authorization": f"Bearer {API_TOKEN}"}
     ```
2. Si la API pagina resultados (como suele pasar con catálogos grandes),
   agregar un bucle de paginación similar al usado en el proyecto de
   web scraping.

## Tecnologías

Python · Requests · SQLite3 · Diseño de esquemas relacionales · CLI con argparse

## Ejemplo de salida

```
Sincronizando inventario desde: https://fakestoreapi.com/products
Productos obtenidos de la API: 20

📊 Resultado de la sincronización:
   🆕 Nuevos:        1
   🔄 Actualizados:  2
   ✅ Sin cambios:   1
   🗑️  Eliminados:    1
   ⚠️  Errores:       0

📦 Estado actual del inventario:
   Total de productos: 4
   Stock total:        927
   Precio promedio:    $90.06
```

---
*Proyecto desarrollado como muestra de trabajo — adaptable a la sincronización
de inventario entre cualquier API de e-commerce/ERP y una base de datos SQL.*
