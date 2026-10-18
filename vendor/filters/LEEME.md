# Filtros de cámara virtual

Aquí van las DLL del filtro DirectShow que la aplicación registra para aparecer
como cámara en Windows. Ver `docs/CAMARA-VIRTUAL.md`.

Esta carpeta es solo el **origen**. `install-camera` no registra las DLL desde
aquí: las copia a `%LOCALAPPDATA%\camvoice\filters\<arquitectura>-<hash>\` y
registra esa copia en el registro del usuario (`HKCU`), sin `regsvr32` ni
permisos de administrador. Por eso mover o borrar la carpeta del programa no
rompe la cámara.

Solo se aceptan las DLL cuyo SHA-256 está en `UNITYCAPTURE_FILTERS`
(`camvoice/output/filters.py`): las claves del registro se escriben a mano a
partir de lo que su `DllRegisterServer` escribe, y con otra DLL la cámara
quedaría rota. Para cambiar de filtro, ver
`docs/VALIDACION_INSTALACION_USUARIO.md` §2.

Se esperan dos archivos, y hacen falta los dos:

    UnityCaptureFilter64.dll   (o el filtro propio, x64)
    UnityCaptureFilter32.dll   (o el filtro propio, x86)

Los dos, porque un filtro DirectShow solo se carga en procesos de su misma
arquitectura: una aplicación de videollamada de 32 bits no verá un filtro de
64 bits.

El nombre se clasifica por arquitectura buscando «32» o «64» en él
(`camvoice/output/filters.py`, función `bundled_filters`).

Una vez puestas:

    camvoice-cli install-camera     # registra solo para tu usuario, sin administrador
    camvoice-cli verify-camera      # registro, enumeración 64/32 bits y fotogramas
    camvoice-cli cameras            # qué hay registrado (usuario / sistema)
    camvoice-cli install-camera --uninstall
