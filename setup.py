from setuptools import setup


setup(
    name="helm-agent-ops",
    version="0.5.12",
    description="Stability-first operations CLI for long-lived agent workspaces.",
    py_modules=["helm", "helm_workspace", "helm_context"],
    packages=["scripts"],
    entry_points={"console_scripts": ["helm=helm:main"]},
)
