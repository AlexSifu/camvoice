# Procedencia y licencia del filtro de cámara virtual

## Archivos

| Archivo | Arquitectura | Tamaño | Origen |
|---|---|---|---|
| `UnityCaptureFilter64.dll` | x64 | 157.696 bytes | [UnityCapture/Install](https://github.com/schellingb/UnityCapture/tree/master/Install) |
| `UnityCaptureFilter32.dll` | x86 | 168.448 bytes | igual |

Descargados el 2026-10-17 de la rama `master` del repositorio oficial, por
HTTPS. Cabecera PE verificada y arquitectura comprobada en el momento de la
descarga (`tests/test_filters.py::test_las_dll_incluidas_existen_y_son_binarios_pe`
lo vuelve a comprobar en cada ejecución de las pruebas).

## Autoría

**UnityCapture**, de Bernhard Schelling.
https://github.com/schellingb/UnityCapture

## Licencia

El repositorio **no incluye un archivo LICENSE**: la declaración está en su
`README.md`, y dice literalmente:

> Unity Capture is divided into two parts that are separately licensed.
> The filter 'UnityCaptureFilter' is available under the **MIT license**.
> The Unity plugin 'UnityCapturePlugin' is available under the zlib license.

Aquí solo se redistribuye **`UnityCaptureFilter`**, la parte bajo **MIT**. El
plugin de Unity, bajo zlib, no se usa ni se incluye.

La licencia MIT permite la redistribución, incluida la de binarios, en producto
abierto o cerrado, con la condición de conservar el aviso de copyright y de
licencia. Este archivo cumple esa condición y debe acompañar a las DLL en
cualquier distribución.

### Nota para una distribución comercial

Como el repositorio no publica el texto íntegro de la licencia MIT con su línea
de copyright, conviene pedir al autor una confirmación por escrito o una copia
del `LICENSE` antes de distribuir el producto comercialmente. Para uso interno y
evaluación, la declaración del README es suficientemente clara.

## Qué hace falta saber para sustituirlo

Nada del código Python depende de que el filtro sea este. `camvoice` detecta
cualquier filtro DirectShow registrado y elige el backend solo
(`camvoice/output/filters.py`). Para cambiar de filtro basta con poner otras DLL
en este directorio; ver `docs/CAMARA-VIRTUAL.md` §7.
