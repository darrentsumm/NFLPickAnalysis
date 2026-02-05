import modal
import subprocess

app = modal.App("nfl-spread-dashboard")

image = (
    modal.Image.debian_slim()
    .pip_install(
        "streamlit",
        "pandas",
        "altair",
        "supabase",
        "python-dotenv"
    )

    .add_local_file("PoolHost_dashboard.py", "/root/PoolHost_dashboard.py")
)

@app.function(
    image=image,
    secrets=[modal.Secret.from_dotenv()] 
)
@modal.web_server(8501)
def run():
    """
    Starts a Streamlit server inside the Modal container.

    This function is a modal.web_server and will start a Streamlit server on port 8501 when invoked.
    The server will run the app located at /root/app.py inside the container.
    The server will be configured to disable CORS and XSRF protection.
    """
    target = "/root/PoolHost_dashboard.py"
    cmd = f"streamlit run {target} --server.port 8501 --server.enableCORS=false --server.enableXsrfProtection=false --server.address=0.0.0.0"
    subprocess.Popen(cmd, shell=True)