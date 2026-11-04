"""Package configuration for WTH-AI."""

from setuptools import find_packages, setup

setup(
    name="wth-ai",
    version="1.0.0",
    description="Mythos-class dual-agent cybersecurity research CLI",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.1",
        "rich>=13.6",
        "python-dotenv>=1.0",
        "prompt-toolkit>=3.0",
        "requests>=2.31",
        "pyyaml>=6.0",
    ],
    entry_points={"console_scripts": ["wth-ai=wth_ai.cli.entry:main"]},
)
