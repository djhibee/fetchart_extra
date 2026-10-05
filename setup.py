import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="fetchart_extra",
    version="0.9",
    author="shredder5262",
    packages=['beetsplug'],
    author_email="",
    description="Extends the built-in fetchart to download and manage extra album artwork: discart (CD art), back covers, and spines.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/djhibee/fetch_extra",
    python_requires='>=3.6',
    install_requires=[
        "beets>=1.5.0",
        "standard-imghdr",
    ],
)
