document.addEventListener('submit',function(e){var m=e.target.getAttribute('data-confirm');if(m&&!confirm(m))e.preventDefault();});
document.addEventListener('click',function(e){if(e.target.matches('[data-print]')){window.print();}});
// نوع الفحص -> المنطقة + السعر
(function(){
  var mod=document.getElementById('modality'),sel=document.getElementById('exam_type_id'),pr=document.getElementById('price');
  if(!sel)return;
  var opts=Array.prototype.slice.call(sel.querySelectorAll('option[data-modality]'));
  function f(){
    var m=mod?mod.value:'';var cur=sel.options[sel.selectedIndex];
    opts.forEach(function(o){var show=!m||o.dataset.modality===m;o.hidden=!show;o.disabled=!show;});
    if(cur&&cur.disabled)sel.value='';
    var c=sel.options[sel.selectedIndex];
    if(pr)pr.textContent=c&&c.dataset.price?Number(c.dataset.price).toLocaleString('en-US'):'—';
  }
  if(mod)mod.addEventListener('change',f);sel.addEventListener('change',f);f();
})();
