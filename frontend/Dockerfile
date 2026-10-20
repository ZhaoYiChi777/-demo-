FROM nginx:1.27-alpine
COPY index.html admin.html *.js *.css /usr/share/nginx/html/
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
