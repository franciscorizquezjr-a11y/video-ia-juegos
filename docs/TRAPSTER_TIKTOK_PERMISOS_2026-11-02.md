# Trapster: acceso a TikTok de PartyLoot

Revisado el **2 de noviembre de 2026**, Europe/Madrid. Cuenta mostrada por TikTok: **Party Loot**. Aplicación: **Trapster.gg**. El usuario comunica que ha añadido la cuenta y aporta el texto de autorización. No se ha entrado a su panel privado ni leído tokens; el @ exacto no figura en ese texto.

## Permisos registrados

Los cuatro aparecen como obligatorios en la pantalla aportada:

| Pantalla de autorización aportada por el usuario | Correspondencia documental |
|---|---|
| Perfil: avatar y nombre | `user.info.basic` |
| Perfil adicional: descripción corta, enlace y estado de verificación | `user.info.profile` |
| Estadísticas: número de me gusta, seguidores, cuentas seguidas y vídeos | `user.info.stats` |
| Ver vídeos públicos de la cuenta | `video.list` |

Los nombres técnicos se han cotejado con la [documentación oficial de TikTok](https://developers.tiktok.com/docs/en/tiktok-api-scopes); no se ha inspeccionado la respuesta técnica de esta autorización. Basic puede incluir identificadores técnicos de la cuenta. Stats da recuentos, no una lista de identidades de todos tus seguidores.

`video.list` permite listar vídeos públicos de la cuenta; no se limita técnicamente a PAO PAO. La [API de consulta de vídeos](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-query) puede devolver detalles y métricas públicas de vídeos autorizados. No consta en lo mostrado permiso para publicar (`video.publish`), enviar borradores (`video.upload`), borrar contenido, cambiar tu perfil o leer mensajes privados. No se ha concedido control completo de TikTok según esta pantalla.

## Declaración de Trapster y límites

Según su [política de privacidad](https://www.trapster.gg/privacy), recibe perfil público y métricas de las entregas, excluye mensajes privados, contenido no publicado y credenciales. Comparte con estudios el perfil público, enlaces y rendimiento de sus campañas. Declara no vender datos personales. Conserva datos mientras la cuenta esté activa; tras eliminarla, ciertos datos hasta 90 días y agregados anonimizados sin plazo definido. Para acceso, exportación o borrado indica `admin@trapster.gg`.

La política declara una finalidad más limitada que la capacidad técnica de listar todos los vídeos públicos. Esto documenta lo que promete y lo que permite el permiso; no verifica sus registros internos.

Sus [términos, apartados 19–20](https://www.trapster.gg/tos), vinculan la conexión a datos públicos y métricas mediante las API. La pantalla implica además aceptación de sus términos: no se limita al permiso técnico. Versiones publicadas revisadas: términos 9/10/2026; privacidad 18/9/2026. Este informe registra la nueva conexión; no sustituye la revisión completa de todas las cláusulas.

## Retirar acceso

En TikTok: Perfil → menú → Ajustes y privacidad → Seguridad y permisos / Seguridad e inicio de sesión → Gestionar permisos de aplicaciones → Trapster.gg → Eliminar acceso. Los nombres pueden variar. [Ruta documentada por TikTok para gestionar una aplicación vinculada](https://ads.us.tiktok.com/resources/help/article/unlink-tiktok-account-from-existing-tiktok-for-business-account?lang=es).

Revocar detiene el acceso autorizado futuro; no garantiza borrar copias ya recibidas. Para suprimirlas, tramitar la solicitud con Trapster. Desconectar durante una campaña puede afectar al seguimiento; no se ha revocado nada en esta tarea.

## Consecuencia para PAO PAO

Conectar TikTok no confirma su admisión en PAO PAO ni que permita una tercera URL en la misma entrega. YouTube e Instagram sí fueron confirmados previamente por el usuario. Comprobar TikTok en el formulario antes de contar con sus vistas.

Propuesta puntual autorizada por el usuario: **2/11/2026 a las 21:30, Madrid**, con el mismo MP4 y el aviso de contenido de marca. No consta programación o publicación real. La incidencia previa de cero vistas sigue sin diagnóstico confirmado; vincular Trapster no la resuelve por sí mismo. El resto de TikTok sigue pausado.
