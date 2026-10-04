<p align="center"><img src="assets/banner.svg" alt="lock-master" width="100%"></p>

# lock-master

[EN](README.md) | [DE](README_de.md) | **ES** | [JA](README_ja.md) | [RU](README_ru.md) | [ZH](README_zh-Hans.md)

**Sistema de bloqueo de archivos portátil y basado en configuración para la coordinación de proyectos multi-agente.**

lock-master ofrece un protocolo de bloqueo ligero y sin dependencias basado en archivos de texto plano. Un archivo `LOCK*.txt` en un directorio de proyecto señala que el proyecto o un componente está siendo usado -- ningún agente, automatización o bucle autónomo debe modificar esa área mientras exista un bloqueo válido y no expirado.

---

## Características

- **Bloqueo por alcance (scope):** `LOCK.txt` bloquea el proyecto completo; `LOCK.<scope>.txt` bloquea un componente. Varios agentes pueden trabajar en paralelo sobre diferentes scopes del mismo proyecto.
- **Expiración automática:** cada bloqueo tiene una duración `expires_after` configurable (por defecto 24h). Un script de limpieza elimina los bloqueos olvidados.
- **Escaneo de solo lectura:** `lock_scan.py` lista todos los bloqueos activos en las raíces configuradas sin modificar ningún archivo.
- **Caché Markdown:** `lock_scan.py --write-cache` escribe un `LOCK-CACHE.md` para una vista general instantánea sin necesidad de escanear.
- **Prueba en seco (dry-run):** `prune_stale_locks.py --dry-run` muestra previamente qué se eliminaría.
- **Sin dependencias:** librería estándar de Python puro (3.10+).
- **Basado en configuración:** todas las raíces, límites de profundidad, directorios a omitir y destinos de caché se definen en `lock_roots.json` -- sin rutas codificadas en el código.

---

## Inicio rápido

### 1. Copiar los scripts

```
pure-locking/lock_utils.py
pure-locking/lock_scan.py
pure-locking/prune_stale_locks.py
pure-locking/LOCK_TEMPLATE.txt
```

Colócalos en el directorio de tu elección (p. ej. `scripts/`).

### 2. Crear `lock_roots.json`

Copia `pure-locking/lock_roots.example.json`, renómbralo a `lock_roots.json` y sustituye las rutas de marcador de posición por las rutas reales de tus proyectos. El archivo está excluido del control de versiones mediante `.gitignore` (contiene rutas absolutas locales).

```json
{
  "default_max_depth": 4,
  "shallow_depth": 2,
  "skip_dirs": [".git", ".venv", "node_modules", "__pycache__", "build", "dist"],
  "roots": [
    { "path": "/ruta/a/proyecto-a" },
    { "path": "/ruta/a/proyecto-b" },
    { "path": "/ruta/a/arbol-grande", "shallow": true }
  ],
  "caches": [
    {
      "name": "todo-el-sistema",
      "path": "/ruta/a/scripts/LOCK-CACHE.{host}.md"
    }
  ]
}
```

### 3. Crear un bloqueo

Copia `pure-locking/LOCK_TEMPLATE.txt` en el directorio de tu proyecto, rellena los campos y renómbralo a `LOCK.txt` (o `LOCK.<scope>.txt` para bloqueos a nivel de componente):

```
owner: mi-agente
created: 2026-06-14T10:00
expires_after: 24h
mode: hard
purpose: Refactorizando módulo de autenticación
```

### 4. Listar los bloqueos activos

```bash
python lock_scan.py
python lock_scan.py --json
```

### 5. Eliminar los bloqueos expirados

```bash
# Vista previa (seguro):
python prune_stale_locks.py --dry-run

# Eliminar realmente:
python prune_stale_locks.py
```

### 6. Actualizar la caché

```bash
python lock_scan.py --write-cache
```

Escribe `LOCK-CACHE.md` según lo definido en la clave `"caches"` de `lock_roots.json`.

---

## Formato del archivo de bloqueo

Texto plano, un `clave: valor` por línea. Las líneas que comienzan con `#` son comentarios.

| Campo               | Requerido | Ejemplo              | Significado |
|---------------------|-----------|----------------------|-------------|
| `owner`             | sí        | `mi-agente`          | Quién posee el bloqueo. |
| `created`           | sí        | `2026-06-14T10:00`   | Marca de tiempo ISO; base para el cálculo de expiración. |
| `expires_after`     | opcional  | `24h`, `90m`, `2d`   | Cadena de duración. Por defecto: `24h`. |
| `release_condition` | opcional  | `PR fusionado`       | Texto libre: cuándo puede liberarse el bloqueo. |
| `mode`              | opcional  | `hard` \| `soft`     | `hard` = sin cambios (por defecto); `soft` = lecturas/sugerencias permitidas. |
| `purpose`           | opcional  | `Añadiendo feature X` | Descripción en texto libre de lo que está en ejecución. |
| `scope`             | opcional  | `frontend`           | Informativo; el **nombre del archivo** es autoritativo. |

Si `created` está ausente o no es parseable, se usa el mtime del archivo como alternativa.

---

## Convención de alcance (scope)

| Nombre de archivo    | Scope detectado | Qué está bloqueado |
|----------------------|-----------------|--------------------|
| `LOCK.txt`           | `project`       | Directorio completo del proyecto |
| `LOCK.api.txt`       | `api`           | Solo el componente `api` |
| `LOCK.frontend.txt`  | `frontend`      | Solo el componente `frontend` |
| `LOCK.my_scope.txt`  | `my_scope`      | Cualquier subárea con nombre libre |

Expresión regular de detección: `^LOCK(\.[^.]+)?\.txt$` (sin distinción de mayúsculas/minúsculas).

---

## Ciclo de vida

```
RESPETAR  -->  RECLAMAR  -->  LIBERAR
```

1. **RESPETAR:** antes de comenzar a trabajar en un proyecto o componente, comprobar si existe un `LOCK*.txt` activo que cubra esa área. Si existe y no ha expirado, elegir otra tarea o esperar.
2. **RECLAMAR:** crear tu archivo de bloqueo a partir de la plantilla (`owner`, `created`, `expires_after`, `purpose`).
3. **LIBERAR:** **eliminar tu propio archivo de bloqueo** cuando hayas terminado. La liberación activa es obligatoria; el tiempo de espera `expires_after` es solo una red de seguridad para los bloqueos olvidados. Si el trabajo lleva más tiempo del esperado, renueva `created` para evitar la expiración prematura.

---

## Referencia de configuración (`lock_roots.json`)

| Clave               | Tipo     | Por defecto | Descripción |
|---------------------|----------|-------------|-------------|
| `default_max_depth` | int      | `4`         | Profundidad máxima de recursión desde cada raíz. |
| `shallow_depth`     | int      | `2`         | Profundidad para las raíces marcadas con `"shallow": true`. |
| `skip_dirs`         | string[] | `[]`        | Nombres de directorios a omitir completamente (incluido el subárbol). |
| `roots`             | object[] | `[]`        | Lista de `{ "path": "...", "shallow": true/false }`. |
| `caches`            | object[] | `[]`        | Destinos de caché: `{ "name", "path", "filter_prefix?" }`. |

**Campos de entrada de caché:**

| Clave           | Requerido | Descripción |
|-----------------|-----------|-------------|
| `name`          | sí        | Nombre para mostrar usado como título de la caché. |
| `path`          | sí        | Ruta absoluta donde se escribe `LOCK-CACHE.md`. |
| `filter_prefix` | opcional  | Incluir solo los bloqueos cuya ruta comience con este prefijo. |

Si se omite `"caches"`, `--write-cache` escribe un único `LOCK-CACHE.md` junto a `lock_scan.py`.

---

## API de Python

```python
from pathlib import Path
import lock_utils

project = Path("/ruta/a/mi-proyecto")

# Comprobar antes de comenzar el trabajo
active = lock_utils.active_locks(project)
if active:
    print(f"Bloqueado: {active}")
else:
    print("Libre para trabajar.")

# Parsear un archivo de bloqueo específico
data = lock_utils.parse_lock_file(project / "LOCK.txt")
print(data["owner"], data["created"])

# Comprobar expiración
from datetime import datetime
expired = lock_utils.is_expired(project / "LOCK.txt", now=datetime.now())
```

---

## Ejecutar las pruebas

```bash
python -m pytest tests/ -v
```

Requiere `pytest` (`pip install pytest`).

---

## Archivos

```
lock-master/
├── pure-locking/
│   ├── lock_utils.py           # Librería principal: parseo, scope, expiración
│   ├── lock_scan.py            # CLI: listar bloqueos activos, escribir caché
│   ├── prune_stale_locks.py    # CLI: eliminar bloqueos expirados
│   ├── LOCK_TEMPLATE.txt       # Plantilla para crear un nuevo bloqueo
│   └── lock_roots.example.json # Ejemplo de configuración con anotaciones
├── LOCK-SYSTEM.md          # Especificación canónica y referencia del ciclo de vida
├── tests/
│   └── test_smoke.py       # Pruebas de humo
├── LICENSE                 # MIT
├── CHANGELOG.md
├── TODO.md
├── SECURITY.md
├── llms.txt
└── VERSION
```

---

## Requisitos

- Python 3.10+
- Sin dependencias de terceros (solo librería estándar)
- Para las pruebas: `pytest`

---

## Licencia

MIT -- Copyright (c) 2026 Lukas Geiger. Ver [LICENSE](LICENSE).
