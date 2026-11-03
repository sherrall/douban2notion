from setuptools import setup, find_packages

setup(
    version="0.0.15",
    packages=find_packages(),
    install_requires=[
        "requests==2.32.5",
        "pendulum==3.1.0",
        "retrying==1.4.2",
        "notion-client==2.5.0",
        "github-heatmap==1.3.8",
        "python-dotenv==1.2.1",
    ],
    entry_points={
        "console_scripts": [
            "douban = douban2notion.douban:main",
            "heatmap = douban2notion.update_heatmap:main",
        ],
    },
    author="malinkang",
    author_email="linkang.ma@gmail.com",
    description="自动将豆瓣电影和豆瓣读书同步到Notion",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/malinkang/weread2notion-pro",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
)