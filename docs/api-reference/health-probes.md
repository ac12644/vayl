---
description: Liveness and readiness probes for load balancers and Kubernetes.
icon: heart-pulse
---

# Health probes

{% openapi-operation spec="vayl-server" path="/healthz" method="get" %}
[OpenAPI vayl-server](https://raw.githubusercontent.com/ac12644/vayl/main/openapi/vayl-server.yaml)
{% endopenapi-operation %}

{% openapi-operation spec="vayl-server" path="/readyz" method="get" %}
[OpenAPI vayl-server](https://raw.githubusercontent.com/ac12644/vayl/main/openapi/vayl-server.yaml)
{% endopenapi-operation %}
