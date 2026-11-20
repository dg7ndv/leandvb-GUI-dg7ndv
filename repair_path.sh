cp ~/.leandvb-GUI/parameters.json ~/.leandvb-GUI/parameters.json.bak
sed -i 's#"\./"#"/usr/local/bin/"#g' ~/.leandvb-GUI/parameters.json
grep -n '_path' ~/.leandvb-GUI/parameters.json
