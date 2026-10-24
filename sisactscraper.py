"""
sisactscraper.py
La VPN la conecta una persona a mano ANTES de ejecutar el script (el script no la toca).
1) Abre SISACT (http://intranetsisact/sisact/Index.aspx).
2) Inicia sesión con el mismo usuario y contraseña de toques_y_selenium.py (Administrador de
   credenciales de Windows). Sirve tanto si sale el popup de Windows como si es un formulario web.

Uso:
  python sisactscraper.py             # con la VPN ya conectada: abre SISACT e inicia sesión
  python sisactscraper.py --cerrar    # cierra el navegador al terminar (por defecto queda abierto)

La contraseña se guarda una sola vez con: python toques_y_selenium.py --guardar-password
"""
import argparse
import logging
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import SessionNotCreatedException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService

# misma dinámica de login que el portal de distribuidores
from toques_y_selenium import (_credenciales_portal, _esperar_carga, _hay_dialogo_auth,
                               llenar_credenciales_lento)

# ============================ CONFIG ============================
URL_SISACT = "http://intranetsisact/sisact/Index.aspx"
NAVEGADOR = "chrome"          # "chrome" o "edge"
# perfil aparte del de toques_y_selenium para poder correr los dos a la vez
PERFIL_DIR = Path(f"./perfil_sisact_{NAVEGADOR}").resolve()
TIMEOUT_WEB = 40              # segundos esperando la carga de SISACT
ESPERA_DIALOGO = 3            # segundos para que aparezca el popup de Windows tras abrir la URL
TIMEOUT_LOGIN = 20            # segundos esperando que SISACT responda tras enviar las credenciales
# textos del botón de ingreso del formulario (en minúsculas)
TEXTOS_BOTON_LOGIN = ("ingresar", "iniciar", "entrar", "aceptar", "login", "acceder")
# ================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("sisact")


# -------------------------- navegador --------------------------
def abrir_navegador():
    opts = webdriver.EdgeOptions() if NAVEGADOR == "edge" else webdriver.ChromeOptions()
    opts.add_argument("--start-maximized")
    # 'none': get() no espera la carga, así podemos atender el popup de autenticación
    opts.set_capability("pageLoadStrategy", "none")
    opts.add_argument(f"--user-data-dir={PERFIL_DIR}")
    try:
        driver = webdriver.Edge(options=opts) if NAVEGADOR == "edge" else webdriver.Chrome(options=opts)
    except SessionNotCreatedException:
        # un driver viejo en el PATH (ej. C:\chromedriver) no coincide con el navegador:
        # se descarga el que corresponde
        if NAVEGADOR == "edge":
            from webdriver_manager.microsoft import EdgeChromiumDriverManager
            driver = webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=opts)
        else:
            from webdriver_manager.chrome import ChromeDriverManager
            driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=opts)
    log.info("[1/3] Navegador abierto (%s).", NAVEGADOR)
    return driver


# ---------------------------- login ----------------------------
def _campo_password(driver):
    """El input de contraseña visible, buscando también dentro de los frames. None si no hay."""
    driver.switch_to.default_content()
    for el in driver.find_elements(By.CSS_SELECTOR, "input[type=password]"):
        if el.is_displayed():
            return el
    for frame in driver.find_elements(By.CSS_SELECTOR, "iframe, frame"):
        driver.switch_to.default_content()
        try:
            driver.switch_to.frame(frame)
        except Exception:
            continue
        for el in driver.find_elements(By.CSS_SELECTOR, "input[type=password]"):
            if el.is_displayed():
                return el                  # se queda dentro del frame para llenar el formulario
    driver.switch_to.default_content()
    return None


def _campo_usuario(driver, campo_pwd):
    """El último input de texto visible que está antes del de contraseña."""
    candidatos = driver.find_elements(
        By.CSS_SELECTOR, "input[type=text], input[type=email], input:not([type])")
    antes = [el for el in candidatos if el.is_displayed() and driver.execute_script(
        "return arguments[0].compareDocumentPosition(arguments[1]) & Node.DOCUMENT_POSITION_FOLLOWING;",
        el, campo_pwd)]
    return antes[-1] if antes else None


def _boton_login(driver):
    for el in driver.find_elements(
            By.CSS_SELECTOR, "input[type=submit], input[type=button], input[type=image], button, a"):
        if not el.is_displayed():
            continue
        texto = " ".join(filter(None, (el.text, el.get_attribute("value"),
                                       el.get_attribute("id"), el.get_attribute("title")))).lower()
        if any(t in texto for t in TEXTOS_BOTON_LOGIN):
            return el
    return None


def login_formulario(driver, campo_pwd):
    """Llena usuario y contraseña en el formulario web de SISACT y presiona Ingresar."""
    usuario, pwd = _credenciales_portal()
    campo_usr = _campo_usuario(driver, campo_pwd)
    if campo_usr is None:
        raise RuntimeError("Encontré el campo de contraseña pero no el de usuario.")
    log.info("[2/3] SISACT: formulario de login; ingresando credenciales de %s...", usuario)

    campo_usr.clear()
    campo_usr.send_keys(usuario)
    campo_pwd.clear()
    campo_pwd.send_keys(pwd)
    del pwd

    boton = _boton_login(driver)
    if boton is not None:
        try:
            boton.click()
        except Exception:
            driver.execute_script("arguments[0].click();", boton)   # por si algo lo tapa
    else:
        campo_pwd.send_keys(Keys.ENTER)
    driver.switch_to.default_content()

    # aceptado = el formulario de contraseña desaparece
    fin = time.time() + TIMEOUT_LOGIN
    while time.time() < fin:
        time.sleep(1)
        try:
            if _campo_password(driver) is None:
                return True
        except Exception:
            pass                           # la página está recargando
    return False


def login_sisact(driver, url=URL_SISACT):
    driver.get(url)                        # con pageLoadStrategy 'none' vuelve enseguida
    time.sleep(ESPERA_DIALOGO)

    # Caso 1: popup de autenticación de Windows (como el portal de distribuidores)
    if _hay_dialogo_auth(driver):
        log.info("[2/3] SISACT: salió el popup de Windows; tecleando credenciales...")
        if not llenar_credenciales_lento(driver):
            raise RuntimeError("SISACT no aceptó las credenciales en el popup de Windows.")

    _esperar_carga(driver, TIMEOUT_WEB)

    # Caso 2: formulario de login dentro de la página
    campo_pwd = _campo_password(driver)
    if campo_pwd is not None:
        if not login_formulario(driver, campo_pwd):
            raise RuntimeError("SISACT sigue mostrando el login: revisa usuario/contraseña.")
        _esperar_carga(driver, TIMEOUT_WEB)
    else:
        log.info("[2/3] SISACT: no pidió credenciales en la página.")

    log.info("[3/3] SISACT listo: %s | %s", driver.title, driver.current_url)
    return driver


# ---------------------------- main ----------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Abre SISACT e inicia sesión")
    ap.add_argument("--cerrar", action="store_true", help="cerrar el navegador al terminar")
    args = ap.parse_args()

    driver = abrir_navegador()
    try:
        login_sisact(driver)
        if not args.cerrar:
            input("Sesión iniciada. Presiona Enter para cerrar el navegador...")
    except Exception as e:
        log.error("Falló el login: %s", e)
        if not args.cerrar:
            input("Revisa el navegador. Presiona Enter para cerrarlo...")
        raise
    finally:
        driver.quit()
        log.info("Navegador cerrado.")
